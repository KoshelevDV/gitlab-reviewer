"""
Application entry point — wires all components together.

Startup order:
  1. Load config
  2. Setup log buffer
  3. Init SQLite database
  4. Create PromptEngine + QueueManager + Reviewer
  5. Start queue workers
  6. Mount routes (API + Web UI)
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from .api.config import router as config_router
from .api.gitlab_api import router as gitlab_router
from .api.logs_api import router as logs_router
from .api.logs_api import set_log_buffer
from .api.providers import router as providers_router
from .api.queue_api import router as queue_router
from .api.queue_api import set_queue_manager
from .api.reviews import router as reviews_router
from .api.reviews import set_database as reviews_set_db
from .config import CONFIG_PATH, reload_config
from .db import Database
from .log_buffer import setup_log_buffer
from .prompt_engine import PromptEngine
from .queue_manager import QueueManager
from .reviewer import Reviewer, set_database as reviewer_set_db
from .ui.router import mount_ui
from .webhook import make_webhook_router
from .webhook import set_queue_manager as webhook_set_queue


def create_app() -> FastAPI:
    # ----------------------------------------------------------------
    # Config + logging
    # ----------------------------------------------------------------
    cfg = reload_config(CONFIG_PATH)
    logging.basicConfig(
        level=cfg.server.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    logger = logging.getLogger(__name__)
    log_buf = setup_log_buffer(maxlen=cfg.ui.log_buffer_lines)

    logger.info(
        "gitlab-reviewer starting (model=%s, providers=%d, max_concurrent=%d)",
        cfg.model.name, len(cfg.providers), cfg.queue.max_concurrent,
    )

    # ----------------------------------------------------------------
    # Core components
    # ----------------------------------------------------------------
    prompts_dir = (Path(__file__).parent.parent / "prompts").resolve()
    prompts = PromptEngine(prompts_dir)

    queue = QueueManager(
        max_concurrent=cfg.queue.max_concurrent,
        max_size=cfg.queue.max_queue_size,
    )
    reviewer = Reviewer(prompts=prompts, queue=queue)

    db = Database(path="data/reviews.db")

    # ----------------------------------------------------------------
    # FastAPI with lifespan
    # ----------------------------------------------------------------
    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        import asyncio
        # Startup
        log_buf.set_loop(asyncio.get_running_loop())
        await db.init()
        reviewer_set_db(db)
        reviews_set_db(db)
        queue.start(review_fn=reviewer.review_job)
        logger.info("Startup complete — workers running")
        yield
        # Shutdown
        logger.info("Shutting down — draining queue...")
        await queue.drain()
        await db.close()
        logger.info("Shutdown complete")

    app = FastAPI(
        title="gitlab-reviewer",
        version="0.3.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        lifespan=lifespan,
    )

    # Inject singletons
    set_log_buffer(log_buf)
    set_queue_manager(queue)
    webhook_set_queue(queue)

    # ----------------------------------------------------------------
    # Routes
    # ----------------------------------------------------------------
    app.include_router(make_webhook_router())
    app.include_router(config_router)
    app.include_router(providers_router)
    app.include_router(gitlab_router)
    app.include_router(queue_router)
    app.include_router(logs_router)
    app.include_router(reviews_router)

    if cfg.ui.enabled:
        mount_ui(app)

    return app


def main() -> None:
    cfg = reload_config(CONFIG_PATH)
    app = create_app()
    uvicorn.run(
        app,
        host=cfg.server.host,
        port=cfg.server.port,
        log_level=cfg.server.log_level,
    )


if __name__ == "__main__":
    main()
