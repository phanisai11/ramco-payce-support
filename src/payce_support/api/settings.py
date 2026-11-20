"""Environment-driven API settings (no extra deps — stdlib only)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml


def _load_config_file(path: Path | None) -> dict[str, Any]:
    """Optional app.yaml config; env vars always take precedence."""
    if path is None or not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"config file {path} must contain a YAML mapping")
    return data


def _cfg_str(config: dict[str, Any], *keys: str) -> str | None:
    node: Any = config
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    if node is None:
        return None
    text = str(node).strip()
    return text or None


def _cfg_bool(config: dict[str, Any], *keys: str) -> bool | None:
    value = _cfg_str(config, *keys)
    if value is None:
        return None
    return value.lower() in {"on", "true", "1", "yes"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw) if raw else default


ApiAuthMode = Literal["none", "jwt_hs256", "jwt_rs256", "iap"]


@dataclass(frozen=True)
class ApiSettings:
    statutory_rules_path: Path
    approved_routing_policy_path: Path
    max_intake_concurrency: int
    cors_allow_origins: tuple[str, ...]
    auth_mode: ApiAuthMode
    jwt_secret: str | None
    jwt_public_key_pem: str | None
    jwt_audience: str | None
    jwt_issuer: str | None
    iap_audience: str | None
    postgres_dsn: str | None
    gcs_bucket: str | None
    billing_export_jsonl: str | None
    openrouter_api_key: str | None
    openrouter_base_url: str | None
    routing_llm_model: str
    routing_llm_enabled: bool
    llm_provider: str
    bedrock_region: str | None
    bedrock_model: str | None
    bedrock_embedding_model: str | None
    tenant_context_dir: Path
    sla_policy_path: Path
    kb_articles_path: Path
    rag_hyde: bool

    @classmethod
    def from_environ(cls, repo_root: Path | None = None) -> ApiSettings:
        root = repo_root or Path(__file__).resolve().parents[3]
        config_path_raw = os.environ.get("PAYCE_CONFIG_PATH")
        config_path = (
            Path(config_path_raw)
            if config_path_raw
            else root / "app.yaml"
        )
        config = _load_config_file(config_path)
        origins_raw = os.environ.get(
            "PAYCE_CORS_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000,"
            "http://localhost:8000,http://127.0.0.1:8000",
        )
        origins = tuple(o.strip() for o in origins_raw.split(",") if o.strip())
        auth_raw = (os.environ.get("PAYCE_AUTH_MODE") or "none").strip().lower()
        if auth_raw not in {"none", "jwt_hs256", "jwt_rs256", "iap"}:
            auth_raw = "none"
        auth_mode: ApiAuthMode = auth_raw  # type: ignore[assignment]
        jwt_secret = os.environ.get("PAYCE_JWT_SECRET")
        pem_path = os.environ.get("PAYCE_JWT_PUBLIC_KEY_PATH")
        jwt_pem: str | None = os.environ.get("PAYCE_JWT_PUBLIC_KEY_PEM")
        if jwt_pem is None and pem_path:
            path = Path(pem_path)
            if path.is_file():
                jwt_pem = path.read_text(encoding="utf-8")
        dsn = os.environ.get("PAYCE_POSTGRES_DSN") or _cfg_str(
            config, "postgres", "dsn"
        )
        if not (dsn and dsn.strip()):
            dsn = None
        gcs_bucket = os.environ.get("PAYCE_GCS_BUCKET")
        if not (gcs_bucket and gcs_bucket.strip()):
            gcs_bucket = None
        billing_jsonl = os.environ.get("PAYCE_BILLING_EXPORT_JSONL")
        if not (billing_jsonl and billing_jsonl.strip()):
            billing_jsonl = None
        openrouter_key = os.environ.get("OPENROUTER_API_KEY") or _cfg_str(
            config, "openrouter", "api_key"
        )
        if not (openrouter_key and openrouter_key.strip()):
            openrouter_key = None
        llm_file_enabled = _cfg_bool(config, "routing_llm", "enabled")
        llm_env = os.environ.get("PAYCE_ROUTING_LLM")
        llm_raw = (llm_env if llm_env is not None else (
            "on" if llm_file_enabled else "off"
        )).strip().lower()
        routing_llm_enabled = llm_raw in {"on", "true", "1", "yes"}
        llm_provider_raw = (
            os.environ.get("PAYCE_LLM_PROVIDER")
            or _cfg_str(config, "llm", "provider")
            or "openrouter"
        ).strip().lower()
        if llm_provider_raw not in {"openrouter", "bedrock"}:
            llm_provider_raw = "openrouter"
        bedrock_region = (
            os.environ.get("PAYCE_BEDROCK_REGION")
            or _cfg_str(config, "bedrock", "region")
            or None
        )
        bedrock_model = (
            os.environ.get("PAYCE_BEDROCK_MODEL")
            or _cfg_str(config, "bedrock", "model")
            or None
        )
        bedrock_embedding_model = (
            os.environ.get("PAYCE_BEDROCK_EMBEDDING_MODEL")
            or _cfg_str(config, "bedrock", "embedding_model")
            or None
        )
        if routing_llm_enabled and llm_provider_raw == "openrouter" and openrouter_key is None:
            routing_llm_enabled = False
        if routing_llm_enabled and llm_provider_raw == "bedrock":
            try:
                import boto3  # noqa: F401
            except ImportError:
                routing_llm_enabled = False
        openrouter_url = (
            os.environ.get("OPENROUTER_BASE_URL")
            or _cfg_str(config, "openrouter", "base_url")
        )
        if not (openrouter_url and openrouter_url.strip()):
            openrouter_url = None
        return cls(
            statutory_rules_path=_env_path(
                "PAYCE_STATUTORY_RULES_PATH",
                root / "data-room" / "integrations" / "statutory_rules.json",
            ),
            approved_routing_policy_path=_env_path(
                "PAYCE_APPROVED_ROUTING_PATH",
                root / "routing_policy" / "approved.yaml",
            ),
            max_intake_concurrency=max(1, _env_int("PAYCE_MAX_INTAKE_CONCURRENCY", 32)),
            cors_allow_origins=origins if origins else ("http://localhost:3000",),
            auth_mode=auth_mode,
            jwt_secret=jwt_secret.strip() if jwt_secret and jwt_secret.strip() else None,
            jwt_public_key_pem=jwt_pem.strip() if jwt_pem and jwt_pem.strip() else None,
            jwt_audience=os.environ.get("PAYCE_JWT_AUDIENCE")
            or None,
            jwt_issuer=os.environ.get("PAYCE_JWT_ISSUER") or None,
            iap_audience=os.environ.get("PAYCE_IAP_AUDIENCE") or None,
            postgres_dsn=dsn,
            gcs_bucket=gcs_bucket,
            billing_export_jsonl=billing_jsonl,
            openrouter_api_key=openrouter_key,
            openrouter_base_url=openrouter_url,
            routing_llm_model=(
                os.environ.get("PAYCE_ROUTING_MODEL")
                or _cfg_str(config, "routing_llm", "model")
                or "openai/gpt-4o-mini"
            ).strip(),
            routing_llm_enabled=routing_llm_enabled,
            llm_provider=llm_provider_raw,
            bedrock_region=bedrock_region,
            bedrock_model=bedrock_model,
            bedrock_embedding_model=bedrock_embedding_model,
            tenant_context_dir=_env_path(
                "PAYCE_TENANT_CONTEXT_DIR",
                root / "routing_policy" / "tenant_context",
            ),
            sla_policy_path=_env_path(
                "PAYCE_SLA_POLICY_PATH",
                root / "routing_policy" / "sla.yaml",
            ),
            kb_articles_path=_env_path(
                "PAYCE_KB_ARTICLES_PATH",
                root / "data-room" / "data" / "kb_articles.json",
            ),
            rag_hyde=(
                os.environ.get("PAYCE_RAG_HYDE", "1").strip().lower()
                not in {"0", "false", "no", "off"}
            ),
        )
