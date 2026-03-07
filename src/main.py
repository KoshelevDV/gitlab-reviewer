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
from .api.config import set_prompt_engine
from .api.gitlab_api import router as gitlab_router
from .api.health import router as health_router
from .api.health import set_database as health_set_db
from .api.health import set_queue_manager as health_set_queue
from .api.logs_api import router as logs_router
from .api.logs_api import set_log_buffer
from .api.memory_api import router as memory_router
from .api.memory_api import set_memory_store as memory_api_set_store
from .api.metrics_api import router as metrics_router
from .api.notifications_api import router as notifications_router
from .api.providers import router as providers_router
from .api.queue_api import router as queue_router
from .api.queue_api import set_queue_manager
from .api.reviews import router as reviews_router
from .api.reviews import set_database as reviews_set_db
from .api.reviews import set_queue_manager as reviews_set_queue
from .api.rules_api import router as rules_router
from .api.targets import router as targets_router
from .backends import create_queue_manager
from .config import CONFIG_PATH, reload_config
from .db import Database
from .log_buffer import setup_log_buffer
from .memory_store import MemoryStore
from .prompt_engine import PromptEngine
from .reviewer import Reviewer, set_memory_store
from .reviewer import set_database as reviewer_set_db
from .ui.router import mount_ui
from .webhook import make_webhook_router, set_rules_path
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
        "gitlab-reviewer starting (model=%s, providers=%d, max_concurrent=%d, queue=%s)",
        cfg.model.name,
        len(cfg.providers),
        cfg.queue.max_concurrent,
        cfg.queue.backend,
    )

    # ----------------------------------------------------------------
    # Core components
    # ----------------------------------------------------------------
    prompts_dir = (Path(__file__).parent.parent / "prompts").resolve()
    prompts = PromptEngine(prompts_dir)
    set_prompt_engine(prompts)

    queue = create_queue_manager(cfg)
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
        # MemoryStore singleton — model loaded once, shared across all reviews
        memory_store = MemoryStore(url=cfg.memory.qdrant_url, collection=cfg.memory.collection)
        set_memory_store(memory_store)
        memory_api_set_store(memory_store)
        reviews_set_db(db)
        reviews_set_queue(queue)
        health_set_db(db)
        health_set_queue(queue)
        # Restore dedup cache from recent DB records (survives service restarts)
        await queue.load_seen_from_db(db)
        if cfg.review.pipeline_v2:
            logger.info("v2 pipeline enabled — using review_job_v2")
            queue.start(review_fn=reviewer.review_job_v2)
        else:
            queue.start(review_fn=reviewer.review_job)
        logger.info("Startup complete — workers running")
        yield
        # Shutdown
        logger.info("Shutting down — draining queue...")
        reviewer.cancel_pending()
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

    # Automation rules: rules.yml sits next to config.yml
    import os as _os

    _rules_yml = _os.path.join(_os.path.dirname(str(CONFIG_PATH.resolve())), "rules.yml")
    set_rules_path(_rules_yml)

    # ----------------------------------------------------------------
    # Routes
    # ----------------------------------------------------------------
    app.include_router(make_webhook_router())
    app.include_router(health_router)
    app.include_router(metrics_router)
    app.include_router(notifications_router)
    app.include_router(config_router)
    app.include_router(providers_router)
    app.include_router(targets_router)
    app.include_router(gitlab_router)
    app.include_router(queue_router)
    app.include_router(logs_router)
    app.include_router(reviews_router)
    app.include_router(memory_router)
    app.include_router(rules_router)

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
