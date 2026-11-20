"""Process-wide service singletons and concurrency guards."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from pathlib import Path
from uuid import UUID

logger = logging.getLogger("payce_support.api.state")

from payce_support.cost_ledger import JsonlBillingExport

from payce_support.incident_cluster import IncidentClusterEngine
from payce_support.intake import ImmutableIntakeService, InMemoryIntakeRepository, IntakeRepository
from payce_support.pipeline import (
    InMemoryTicketPipelineStore,
    TicketEventProcessor,
    TicketPipelineStore,
)
from payce_support.pubsub_consumer import TicketIngestedConsumer
from payce_support.routing_signals import LlmSignalExtractor, SignalExtractor
from payce_support.postgres import PostgresIntakeRepository, PostgresObjectManifestRepository
from payce_support.routing import RoutingDecisionRepository, RoutingPolicy
from payce_support.routing_matcher import load_approved_routing_policy
from payce_support.statutory_engine import StatutoryEngine
from payce_support.storage import (
    ImmutableObjectStore,
    InMemoryImmutableObjectStore,
    InMemoryObjectManifestRepository,
    ObjectManifestRepository,
)
from payce_support.tenancy import TenantResolver

from payce_support.api.settings import ApiSettings


def _default_tenant_resolver() -> TenantResolver:
    from payce_support.models import Region, TenantRecord

    tenants = (
        TenantRecord(
            tenant_id="C-1001",
            tenant_uuid=UUID("00000000-0000-4000-8000-000000000001"),
            region=Region.INDIA,
        ),
        TenantRecord(
            tenant_id="C-1002",
            tenant_uuid=UUID("00000000-0000-4000-8000-000000000002"),
            region=Region.INDIA,
        ),
    )
    return TenantResolver(
        tenants,
        verified_contacts={
            "contact-a": {"C-1001"},
            "conflicted": {"C-1001", "C-1002"},
        },
        mapping_version="contacts-v1",
    )


def _intake_backends(
    settings: ApiSettings,
) -> tuple[ImmutableObjectStore, ObjectManifestRepository, IntakeRepository]:
    if settings.postgres_dsn:
        manifests: ObjectManifestRepository = PostgresObjectManifestRepository(settings.postgres_dsn)
        intake_repo: IntakeRepository = PostgresIntakeRepository(settings.postgres_dsn)
    else:
        manifests = InMemoryObjectManifestRepository()
        intake_repo = InMemoryIntakeRepository()

    if settings.gcs_bucket:
        from payce_support.gcs_store import GcsImmutableObjectStore

        object_store = GcsImmutableObjectStore(bucket_name=settings.gcs_bucket)
    else:
        # Local dev: durable filesystem store so raw bodies and attachment
        # bytes (screenshots, PDFs, call recordings) survive restarts.
        from payce_support.storage import LocalDiskImmutableObjectStore

        object_store = LocalDiskImmutableObjectStore("data/objects")
    return object_store, manifests, intake_repo


def build_intake_service(
    *,
    resolver: TenantResolver,
    max_attachment_zip_uncompressed_bytes: int | None,
    settings: ApiSettings,
) -> tuple[ImmutableIntakeService, ObjectManifestRepository, ImmutableObjectStore]:
    object_store, manifests, intake_repo = _intake_backends(settings)
    service = ImmutableIntakeService(
        tenant_resolver=resolver,
        object_store=object_store,
        manifests=manifests,
        intake_repository=intake_repo,
        max_attachment_zip_uncompressed_bytes=max_attachment_zip_uncompressed_bytes,
    )
    return service, manifests, object_store


@dataclass
class AppState:
    settings: ApiSettings
    statutory: StatutoryEngine
    routing_policy_data: dict
    routing_policy: RoutingPolicy
    intake: ImmutableIntakeService
    resolver: TenantResolver
    incident: IncidentClusterEngine
    routing_decisions: RoutingDecisionRepository
    pipeline: TicketPipelineStore
    processor: TicketEventProcessor
    pubsub_consumer: TicketIngestedConsumer
    manifests: ObjectManifestRepository | None = None
    object_store: ImmutableObjectStore | None = None
    billing_export: JsonlBillingExport | None = None
    signal_extractor: SignalExtractor | None = None
    queue_suggester: Any | None = None
    chat_model: Any | None = None
    triage_flow: Any | None = None
    ai_transport: Any | None = None
    rag_composer: Any | None = None
    rag_composer_fallback: Any | None = None
    kb_store: Any | None = None
    tenant_contexts: dict = field(default_factory=dict)
    sla_policy: dict = field(default_factory=dict)
    routing_policy_path: Path | None = None
    sla_policy_path: Path | None = None
    incident_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    intake_semaphore: asyncio.Semaphore = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "intake_semaphore",
            asyncio.Semaphore(self.settings.max_intake_concurrency),
        )


def _ensure_embedded_postgres(dsn: str, root: Path) -> None:
    """Auto-start the pgserver cluster when the DSN uses a local socket dir.

    The dev DSN looks like ``postgresql://user@/db?host=/abs/path/pgdata``.
    Only directories that look like pgserver clusters are managed; remote
    DSNs are left alone.
    """
    try:
        from urllib.parse import parse_qs, urlparse

        host = parse_qs(urlparse(dsn).query).get("host", [None])[0]
        if not host:
            return
        pgdata = Path(host)
        if not pgdata.is_absolute():
            pgdata = root / pgdata
        if "pgdata" not in pgdata.parts:
            return
        import pgserver

        srv = pgserver.get_server(pgdata, cleanup_mode=None)
        srv.ensure_postgres_running()
        logger.info("embedded postgres ready at %s", pgdata)
    except Exception:
        logger.warning("embedded postgres autostart failed", exc_info=True)


def _build_embedder(
    settings: ApiSettings,
    manifest: dict,
    llm_provider: str,
    ai_transport: Any | None,
) -> Any | None:
    """Construct the embedding model for the configured LLM provider.

    Returns ``None`` when the provider's embedding path isn't configured.
    """
    if not settings.routing_llm_enabled:
        return None
    if llm_provider == "bedrock":
        try:
            from payce_support.bedrock import (
                DEFAULT_EMBEDDING_MODEL,
                BedrockEmbeddingModel,
            )

            return BedrockEmbeddingModel(
                model_id=settings.bedrock_embedding_model or DEFAULT_EMBEDDING_MODEL,
                region_name=settings.bedrock_region,
            )
        except Exception:
            logger.warning("bedrock embedding model init failed", exc_info=True)
            return None
    if ai_transport is not None:
        from payce_support.openrouter import OpenRouterEmbeddingModel

        return OpenRouterEmbeddingModel(
            transport=ai_transport,
            model_id=manifest["embedding_model_id"],
            default_dimensions=manifest.get("embedding_dimensions"),
        )
    return None


def _build_triage_flow(
    *,
    extractor: Any,
    language_model: Any,
    routing_policy_data: dict,
    routing_policy: RoutingPolicy,
) -> Any | None:
    """LangGraph triage flow — optional; None when langgraph isn't installed."""
    try:
        from payce_support.flows.intake_triage import IntakeTriageFlow
    except ImportError:
        return None
    try:
        return IntakeTriageFlow(
            extractor=extractor,
            language_model=language_model,
            routing_policy_data=routing_policy_data,
            routing_policy=routing_policy,
        )
    except Exception:
        logger.warning("triage flow init failed", exc_info=True)
        return None


def build_app_state(settings: ApiSettings, *, repo_root: Path | None = None) -> AppState:
    root = repo_root or Path(__file__).resolve().parents[3]
    if settings.postgres_dsn:
        _ensure_embedded_postgres(settings.postgres_dsn, root)
    statutory_path = settings.statutory_rules_path
    if not statutory_path.is_absolute():
        statutory_path = root / statutory_path
    routing_path = settings.approved_routing_policy_path
    if not routing_path.is_absolute():
        routing_path = root / routing_path

    statutory = StatutoryEngine.load(statutory_path)
    routing_data = load_approved_routing_policy(routing_path)
    approved_ids = frozenset(str(x) for x in routing_data["approved_rule_ids"])
    if settings.postgres_dsn:
        from payce_support.postgres_pipeline import (
            PostgresRoutingDecisionRepository,
            PostgresTicketPipelineStore,
        )

        routing_decisions: RoutingDecisionRepository = PostgresRoutingDecisionRepository(
            settings.postgres_dsn,
        )
        pipeline: TicketPipelineStore = PostgresTicketPipelineStore(settings.postgres_dsn)
    else:
        from payce_support.routing import InMemoryRoutingDecisionRepository

        routing_decisions = InMemoryRoutingDecisionRepository()
        pipeline = InMemoryTicketPipelineStore()
    routing_policy = RoutingPolicy(
        policy_version=str(routing_data["policy_version"]),
        approved_rule_ids=approved_ids,
        repository=routing_decisions,
    )
    resolver = _default_tenant_resolver()

    import json

    manifest_path = root / "eval" / "fixtures" / "manifest.json"
    zip_limit: int | None = None
    if manifest_path.is_file():
        zip_limit = int(json.loads(manifest_path.read_text(encoding="utf-8"))["g2_boundary"]["zip_max_uncompressed_bytes"])

    intake, manifests, object_store = build_intake_service(
        resolver=resolver,
        max_attachment_zip_uncompressed_bytes=zip_limit,
        settings=settings,
    )
    processor = TicketEventProcessor(
        store=pipeline,
        routing_policy=routing_policy,
        routing_policy_data=routing_data,
        manifests=manifests,
        object_store=object_store,
    )

    from payce_support.tenant_context import load_tenant_contexts

    tenant_contexts = load_tenant_contexts(settings.tenant_context_dir)
    from payce_support.sla import load_sla_policy, load_sla_policy_from_db

    sla_path = settings.sla_policy_path
    if not sla_path.is_absolute():
        sla_path = root / sla_path
    sla_policy: dict = {"rules": []}
    if settings.postgres_dsn:
        try:
            sla_policy = load_sla_policy_from_db(settings.postgres_dsn)
        except Exception:
            logger.warning(
                "app.sla_rule unavailable; falling back to sla.yaml",
                exc_info=True,
            )
            sla_policy = (
                load_sla_policy(sla_path) if sla_path.is_file() else {"rules": []}
            )
    elif sla_path.is_file():
        sla_policy = load_sla_policy(sla_path)

    signal_extractor: SignalExtractor | None = None
    queue_suggester = None
    ai_transport = None
    chat_model = None
    llm_provider = getattr(settings, "llm_provider", "openrouter")
    llm_ready = settings.routing_llm_enabled and (
        llm_provider == "bedrock" or bool(settings.openrouter_api_key)
    )
    if llm_provider == "openrouter" and settings.openrouter_api_key:
        from payce_support.openrouter import OpenRouterClient

        client_kwargs: dict = {"api_key": settings.openrouter_api_key}
        if settings.openrouter_base_url:
            client_kwargs["base_url"] = settings.openrouter_base_url
        ai_transport = OpenRouterClient(**client_kwargs)
    if llm_ready:
        if llm_provider == "bedrock":
            try:
                from payce_support.bedrock import (
                    BedrockChatModel,
                    resolve_nova_model,
                )

                bedrock_model = settings.bedrock_model or "lite"
                if bedrock_model in {"micro", "lite", "pro"}:
                    bedrock_model = resolve_nova_model(bedrock_model)
                chat_model = BedrockChatModel(
                    model_id=bedrock_model,
                    region_name=settings.bedrock_region,
                )
            except Exception:
                logger.warning("bedrock chat model init failed", exc_info=True)
                chat_model = None
        elif ai_transport is not None:
            from payce_support.openrouter import OpenRouterChatModel

            chat_model = OpenRouterChatModel(
                transport=ai_transport,
                model_id=settings.routing_llm_model,
            )
    if chat_model is not None:
        from payce_support.llm_tracer import TracingLanguageModel

        chat_model = TracingLanguageModel(chat_model)
        signal_extractor = LlmSignalExtractor(
            language_model=chat_model,
            tenant_contexts=tenant_contexts,
        )
        from payce_support.queue_suggester import LlmQueueSuggester

        queue_suggester = LlmQueueSuggester(
            language_model=chat_model,
            tenant_contexts=tenant_contexts,
        )

    # RAG draft composer: local KB retriever + hash embeddings always work;
    # the language model is OpenRouter when keyed, else the deterministic
    # extractive fallback so the flow is demoable offline.
    rag_composer = None
    rag_composer_fallback = None
    kb_store = None
    kb_path = settings.kb_articles_path
    if not kb_path.is_absolute():
        kb_path = root / kb_path
    if kb_path.is_file():
        from payce_support.kb_local import (
            LocalExtractiveLanguageModel,
            LocalHashEmbedding,
            LocalKbStore,
            LocalKeywordRetriever,
        )
        from payce_support.rag import (
            InMemoryRagResultCache,
            RagComposer,
            RagPolicy,
        )

        from payce_support.kb_local import load_kb_articles_from_db

        kb_store = LocalKbStore(
            kb_path,
            articles_loader=(
                (lambda: load_kb_articles_from_db(settings.postgres_dsn))
                if settings.postgres_dsn
                else None
            ),
        )

        def _tenant_kb_ids() -> dict[str, frozenset[str]]:
            # Live view: tenant_contexts is hot-reloaded on edits, so RAG
            # scoping always reflects the latest kb_hints per tenant.
            # Returns allowed *topic tokens* per tenant (from hint kb_id +
            # hint topics); a shared chunk is eligible when its kb_id/topic
            # shares a token — hits stay unique per tenant.
            out: dict[str, frozenset[str]] = {}
            for tid, ctx in tenant_contexts.items():
                tokens: set[str] = set()
                for h in ctx.kb_hints:
                    for raw in (h.kb_id, *h.topics):
                        tokens.update(
                            t
                            for t in re.split(r"[^a-z0-9]+", raw.lower())
                            if len(t) >= 2 and t not in {"kb", "the", "and", "for", "in", "of"}
                        )
                out[tid] = frozenset(tokens)
            return out

        # Vector RAG path: when kb_docs have been ingested into
        # app.shared_kb_chunk (SNAPSHOT.json present) and Postgres + an
        # embedding provider are configured, retrieve real embedded chunks
        # via pgvector instead of keyword-overlap stubs. The snapshot's
        # embedding model/dimensions must match the configured provider —
        # vectors from different models live in incompatible spaces, so a
        # mismatch falls back to the local KB until re-ingestion.
        vector_rag: tuple[Any, Any, str, str] | None = None
        kb_docs_dir = root / "data-room" / "kb_docs"
        snap_path = kb_docs_dir / "SNAPSHOT.json"
        if snap_path.is_file() and settings.postgres_dsn:
            try:
                import json as _json

                from payce_support.postgres_retrieval import (
                    DirectAsyncConnectionPool,
                    PostgresKnowledgeRetriever,
                )

                manifest = _json.loads(snap_path.read_text(encoding="utf-8"))
                embedder = _build_embedder(settings, manifest, llm_provider, ai_transport)
                if embedder is not None and (
                    embedder.model_id == manifest.get("embedding_model_id")
                    and getattr(embedder, "dimensions", manifest.get("embedding_dimensions"))
                    == manifest.get("embedding_dimensions")
                ):
                    vector_rag = (
                        PostgresKnowledgeRetriever(
                            pool=DirectAsyncConnectionPool(settings.postgres_dsn)
                        ),
                        embedder,
                        manifest["snapshot_version"],
                        manifest["embedding_profile"],
                    )
                    logger.info(
                        "vector RAG enabled: snapshot=%s chunks=%s provider=%s",
                        manifest["snapshot_version"], manifest.get("chunks"),
                        llm_provider,
                    )
                elif embedder is not None:
                    logger.warning(
                        "KB snapshot embedder %s/%sd does not match configured %s; "
                        "re-run scripts/kb_ingest.py to rebuild vectors",
                        manifest.get("embedding_model_id"),
                        manifest.get("embedding_dimensions"),
                        embedder.model_id,
                    )
            except Exception:
                logger.warning("vector RAG setup failed; using local KB", exc_info=True)
                vector_rag = None

        def _composer(language_model) -> Any:
            if vector_rag is not None:
                retriever, embedder, snap, profile = vector_rag
                from payce_support.hybrid_retrieval import HybridRetriever
                from payce_support.postgres_retrieval import (
                    DirectAsyncConnectionPool,
                    PostgresLexicalRetriever,
                )

                # Hybrid retrieval: pgvector semantic arm + keyword arm over
                # the same shared_kb_chunk corpus, fused via RRF.
                retriever = HybridRetriever(
                    vector=retriever,
                    keyword=PostgresLexicalRetriever(
                        pool=DirectAsyncConnectionPool(settings.postgres_dsn)
                    ),
                )
            else:
                retriever = LocalKeywordRetriever(kb_store)
                embedder = LocalHashEmbedding()
                snap = kb_store.snapshot_version
                profile = "local-hash-v1"
            return RagComposer(
                embedding_model=embedder,
                retriever=retriever,
                language_model=language_model,
                cache=InMemoryRagResultCache(),
                policy=RagPolicy(
                    prompt_version="rag-draft-v2",
                    kb_snapshot_version=snap,
                    embedding_profile=profile,
                    minimum_retrieval_score=0.20,
                    top_k=3,
                    hyde=settings.rag_hyde,
                ),
                tenant_kb_ids=_tenant_kb_ids,
            )

        rag_composer_fallback = _composer(LocalExtractiveLanguageModel())
        if chat_model is not None:
            rag_composer = _composer(chat_model)
        else:
            rag_composer = rag_composer_fallback

    return AppState(
        settings=settings,
        statutory=statutory,
        routing_policy_data=routing_data,
        routing_policy=routing_policy,
        intake=intake,
        resolver=resolver,
        incident=IncidentClusterEngine(),
        routing_decisions=routing_decisions,
        pipeline=pipeline,
        processor=processor,
        pubsub_consumer=TicketIngestedConsumer(
            processor=processor,
            manifests=manifests,
            object_store=object_store,
        ),
        manifests=manifests,
        object_store=object_store,
        billing_export=(
            JsonlBillingExport(settings.billing_export_jsonl)
            if settings.billing_export_jsonl
            else None
        ),
        signal_extractor=signal_extractor,
        queue_suggester=queue_suggester if chat_model is not None else None,
        chat_model=chat_model,
        triage_flow=_build_triage_flow(
            extractor=signal_extractor,
            language_model=chat_model,
            routing_policy_data=routing_data,
            routing_policy=routing_policy,
        ),
        ai_transport=ai_transport,
        rag_composer=rag_composer,
        rag_composer_fallback=rag_composer_fallback,
        kb_store=kb_store,
        tenant_contexts=tenant_contexts,
        sla_policy=sla_policy,
        routing_policy_path=routing_path,
        sla_policy_path=sla_path,
    )
