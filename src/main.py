"""
Application entry point — wires all components together.

Startup order:
  1. Load config
  2. Setup log buffer (attach to root logger)
  3. Create PromptEngine
  4. Create QueueManager
  5. Create Reviewer
  6. Start queue workers
  7. Mount Web UI + API routes
"""
from __future__ import annotations

import logging

import uvicorn
from fastapi import FastAPI

from .api.config import router as config_router
from .api.gitlab_api import router as gitlab_router
from .api.logs_api import router as logs_router
from .api.logs_api import set_log_buffer
from .api.providers import router as providers_router
from .api.queue_api import router as queue_router
from .api.queue_api import set_queue_manager
from .config import CONFIG_PATH, reload_config
from .log_buffer import setup_log_buffer
from .prompt_engine import PromptEngine
from .queue_manager import QueueManager
from .reviewer import Reviewer
from .ui.router import mount_ui
from .webhook import make_webhook_router, set_queue_manager as webhook_set_queue


def create_app() -> FastAPI:
    # ----------------------------------------------------------------
    # 1. Config
    # ----------------------------------------------------------------
    cfg = reload_config(CONFIG_PATH)

    # ----------------------------------------------------------------
    # 2. Logging
    # ----------------------------------------------------------------
    logging.basicConfig(
        level=cfg.server.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    logger = logging.getLogger(__name__)
    log_buf = setup_log_buffer(maxlen=cfg.ui.log_buffer_lines)

    logger.info(
        "Starting gitlab-reviewer (model=%s, max_concurrent=%d)",
        cfg.model.name, cfg.queue.max_concurrent,
    )

    # ----------------------------------------------------------------
    # 3. Core components
    # ----------------------------------------------------------------
    prompts = PromptEngine(_default_prompts_dir())
    queue = QueueManager(
        max_concurrent=cfg.queue.max_concurrent,
        max_size=cfg.queue.max_queue_size,
    )
    reviewer = Reviewer(prompts=prompts, queue=queue)

    # ----------------------------------------------------------------
    # 4. FastAPI app
    # ----------------------------------------------------------------
    app = FastAPI(
        title="gitlab-reviewer",
        version="0.2.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
    )

    # Inject singletons into API modules
    set_log_buffer(log_buf)
    set_queue_manager(queue)
    webhook_set_queue(queue)

    # Wire log buffer to event loop after startup
    @app.on_event("startup")
    async def _startup() -> None:
        import asyncio
        log_buf.set_loop(asyncio.get_event_loop())
        queue.start(review_fn=reviewer.review_job)
        logger.info("Review workers started (max_concurrent=%d)", cfg.queue.max_concurrent)

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        logger.info("Shutting down — draining queue...")
        await queue.drain()

    # ----------------------------------------------------------------
    # 5. Routes
    # ----------------------------------------------------------------
    app.include_router(make_webhook_router())
    app.include_router(config_router)
    app.include_router(providers_router)
    app.include_router(gitlab_router)
    app.include_router(queue_router)
    app.include_router(logs_router)

    if cfg.ui.enabled:
        mount_ui(app)

    return app


def _default_prompts_dir():
    from pathlib import Path
    return (Path(__file__).parent.parent / "prompts").resolve()


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
