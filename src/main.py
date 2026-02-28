"""Application entry point."""
from __future__ import annotations

import logging

import uvicorn
from fastapi import FastAPI

from .config import Settings, load_review_config
from .gitlab_client import GitLabClient
from .llm_client import LLMClient
from .prompt_engine import PromptEngine
from .reviewer import ReviewConfig, Reviewer
from .webhook import make_webhook_router


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or Settings()  # type: ignore[call-arg]
    review_cfg_raw = load_review_config(cfg.config_file)

    logging.basicConfig(
        level=cfg.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    logger = logging.getLogger(__name__)
    logger.info("Starting gitlab-reviewer (dry_run=%s, model=%s)", cfg.dry_run, cfg.ollama_model)

    # Wire dependencies
    gitlab = GitLabClient(cfg.gitlab_url, cfg.gitlab_token)
    llm = LLMClient(cfg.ollama_url, cfg.ollama_model, timeout=cfg.llm_timeout)
    prompts = PromptEngine(cfg.prompts_dir)

    rev_cfg = ReviewConfig(
        system_prompt_names=review_cfg_raw["prompts"]["system"],
        whitelist_authors=review_cfg_raw["reviewers"].get("whitelist_authors", []),
        whitelist_projects=review_cfg_raw["reviewers"].get("whitelist_projects", []),
        skip_draft=review_cfg_raw["reviewers"].get("skip_draft", True),
        dry_run=cfg.dry_run,
        max_files=cfg.max_files_per_review,
        max_diff_chars=cfg.llm_max_diff_chars,
        dedup_ttl=cfg.diff_cache_ttl,
        temperature=cfg.llm_temperature,
    )

    reviewer = Reviewer(gitlab, llm, prompts, rev_cfg)

    app = FastAPI(title="gitlab-reviewer", version="0.1.0")
    app.include_router(make_webhook_router(reviewer, cfg.webhook_secret))

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await gitlab.aclose()
        await llm.aclose()

    return app


def main() -> None:
    cfg = Settings()  # type: ignore[call-arg]
    app = create_app(cfg)
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level=cfg.log_level)


if __name__ == "__main__":
    main()
