"""ASGI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from payce_support.api.settings import ApiSettings
from payce_support.api.state import AppState, build_app_state
from payce_support.api.routers.dataroom import router as dataroom_router
from payce_support.api.routers.dashboard import router as dashboard_router
from payce_support.api.routers.health import router as health_router
from payce_support.api.routers.incident import router as incident_router
from payce_support.api.routers.ingest import router as ingest_router
from payce_support.api.routers.pipeline import router as pipeline_router
from payce_support.api.routers.routing import router as routing_router
from payce_support.api.routers.statutory import router as statutory_router
from payce_support.api.routers.triage import router as triage_router


def create_app(*, settings: ApiSettings | None = None, repo_root: Path | None = None) -> FastAPI:
    """Build FastAPI with shared `AppState` on `app.state.payce`."""
    cfg = settings or ApiSettings.from_environ()
    state: AppState | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        import asyncio
        import logging

        nonlocal state
        state = build_app_state(cfg, repo_root=repo_root)
        app.state.payce = state

        async def _auto_close_loop() -> None:
            """Every 6h: close tickets that have been `resolved` > 48h."""
            log = logging.getLogger("payce_support.auto_close")
            while True:
                await asyncio.sleep(6 * 3600)
                try:
                    closed = await asyncio.to_thread(
                        state.pipeline.close_stale_resolved,
                        older_than_hours=48.0,
                    )
                    if closed:
                        log.info("auto-closed %d stale resolved tickets", len(closed))
                except Exception:
                    log.warning("auto-close sweep failed", exc_info=True)

        closer = asyncio.create_task(_auto_close_loop())
        yield
        closer.cancel()
        transport = getattr(state, "ai_transport", None)
        aclose = getattr(transport, "aclose", None)
        if aclose is not None:
            await aclose()

    repo = repo_root or Path(__file__).resolve().parents[3]
    web_dev = repo / "web" / "dev"

    app = FastAPI(
        title="Payce Support Intelligence API",
        version="0.1.0",
        lifespan=lifespan,
    )

    if web_dev.is_dir():
        app.mount(
            "/dev/ui",
            StaticFiles(directory=str(web_dev), html=True),
            name="dev_ui",
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cfg.cors_allow_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health_router)
    app.include_router(ingest_router)
    app.include_router(pipeline_router)
    app.include_router(statutory_router)
    app.include_router(routing_router)
    app.include_router(triage_router)
    app.include_router(incident_router)
    app.include_router(dashboard_router)
    app.include_router(dataroom_router)

    def _openapi_with_security() -> dict:
        from fastapi.openapi.utils import get_openapi

        cached = getattr(app, "openapi_schema", None)
        if cached is not None:
            return cached
        schema = get_openapi(
            title=app.title,
            version=app.version,
            routes=app.routes,
        )
        if cfg.auth_mode != "none":
            schema.setdefault("components", {}).setdefault("securitySchemes", {})
            schemes = schema["components"]["securitySchemes"]
            if cfg.auth_mode == "iap":
                schemes["IAP-JWT"] = {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-Goog-Iap-Jwt-Assertion",
                    "description": "Identity-Aware Proxy assertion (Google-signed JWT)",
                }
                schema["security"] = [{"IAP-JWT": []}]
            else:
                schemes["BearerAuth"] = {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "JWT",
                }
                schema["security"] = [{"BearerAuth": []}]
        app.openapi_schema = schema
        return schema

    # React SPA (web/app/dist) mounted last so API routes keep precedence.
    # The app uses relative fetch URLs, so serving it same-origin means no
    # CORS/rebuild needed. Old JS dashboard stays at /dev/ui.
    web_app = repo / "web" / "app" / "dist"
    if web_app.is_dir():
        app.mount(
            "/",
            StaticFiles(directory=str(web_app), html=True),
            name="web_app",
        )

    app.openapi = _openapi_with_security  # type: ignore[method-assign]

    return app


# Uvicorn default target: `uvicorn payce_support.api.app:app --reload`
app: FastAPI = create_app()
