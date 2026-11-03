import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import timedelta

import httpx
from fastapi import FastAPI

from app import clock
from app.api.chat_routes import router as chat_router
from app.api.routes import router
from app.api.stats_routes import router as stats_router
from app.chat.gemini import GeminiModel
from app.chat.local import LocalModel
from app.config import Settings
from app.db import prepare_database, session_scope
from app.services.catalog import download_catalog, import_catalog, startup_action
from app.services.stats import ensure_question_data
from app.services.retention import purge_snapshots
from app.sources.opensky import OpenSkyClient, UpstreamError

logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("sky_scout")


def create_app(
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    sync_on_startup: bool = True,
    model=None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        sleeper = getattr(app.state, "sleeper", None) or asyncio.sleep
        app.state.sleeper = sleeper
        app.state.http = httpx.AsyncClient(
            timeout=httpx.Timeout(app.state.settings.http_timeout_seconds),
            transport=app.state.transport,
            headers={"User-Agent": "sky-scout/0.1"},
            trust_env=False,
        )
        app.state.opensky = OpenSkyClient(app.state.http, app.state.settings, sleeper)
        if app.state.model_override is not None:
            app.state.model = app.state.model_override
        elif app.state.settings.gemini_api_key:
            app.state.model = GeminiModel(app.state.http, app.state.settings, sleeper)
        else:
            app.state.model = LocalModel()

        async def run_sync():
            csv_text = await download_catalog(app.state.http, app.state.settings, sleeper)
            with session_scope(app.state.session_factory) as session:
                sync = import_catalog(session, csv_text, app.state.settings.ourairports_url)
                return {
                    "row_count": sync.row_count,
                    "fetched_at": sync.fetched_at,
                    "source_url": sync.source_url,
                }

        app.state.run_sync = run_sync
        if app.state.sync_on_startup:
            with session_scope(app.state.session_factory) as session:
                action = startup_action(session)
            if action == "immediate":
                try:
                    await run_sync()
                except UpstreamError:
                    logger.exception("source=ourairports status=error")
            elif action == "background":
                app.state.directory_refresh_task = asyncio.create_task(_background_sync(run_sync))
        if app.state.sync_on_startup:
            app.state.dataset_task = asyncio.create_task(
                _background_datasets(app.state.http, app.state.settings, sleeper, app.state.session_factory)
            )
        with session_scope(app.state.session_factory) as session:
            purge_snapshots(session, clock.utcnow() - timedelta(days=app.state.settings.snapshot_retention_days))
            session.commit()
        try:
            yield
        finally:
            for name in ("directory_refresh_task", "dataset_task"):
                task = getattr(app.state, name, None)
                if task is not None and not task.done():
                    task.cancel()
            await app.state.http.aclose()

    app = FastAPI(title="Sky Scout", lifespan=lifespan)
    app.state.settings = settings or Settings()
    app.state.transport = transport
    app.state.sync_on_startup = sync_on_startup
    app.state.model_override = model
    app.state.session_factory = prepare_database(app.state.settings)
    app.include_router(router)
    app.include_router(stats_router)
    app.include_router(chat_router)
    return app


async def _background_datasets(http, settings, sleeper, session_factory) -> None:
    try:
        await ensure_question_data(http, settings, sleeper, session_factory)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("source=datasets status=error")


async def _background_sync(run_sync) -> None:
    try:
        await run_sync()
    except UpstreamError:
        logger.exception("source=ourairports status=error")
    except asyncio.CancelledError:
        raise


app = create_app()
