"""GitLab API client — fetch MR diffs, post review comments."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)


@dataclass
class MRInfo:
    project_id: int | str
    iid: int
    title: str
    description: str
    author: str
    source_branch: str
    target_branch: str
    is_draft: bool
    web_url: str


@dataclass
class FileDiff:
    old_path: str
    new_path: str
    diff: str
    new_file: bool
    deleted_file: bool
    renamed_file: bool


class GitLabClient:
    def __init__(self, base_url: str, token: str, timeout: int = 30) -> None:
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            headers={"PRIVATE-TOKEN": token, "Content-Type": "application/json"},
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # MR info
    # ------------------------------------------------------------------

    async def get_mr(self, project_id: int | str, mr_iid: int) -> MRInfo:
        pid = quote(str(project_id), safe="")
        resp = await self._client.get(
            f"{self._base}/api/v4/projects/{pid}/merge_requests/{mr_iid}"
        )
        resp.raise_for_status()
        d = resp.json()
        return MRInfo(
            project_id=project_id,
            iid=mr_iid,
            title=d["title"],
            description=d.get("description") or "",
            author=d["author"]["username"],
            source_branch=d["source_branch"],
            target_branch=d["target_branch"],
            is_draft=d.get("draft", False) or d["title"].lower().startswith(("draft:", "wip:")),
            web_url=d["web_url"],
        )

    # ------------------------------------------------------------------
    # Diffs
    # ------------------------------------------------------------------

    async def get_diffs(
        self, project_id: int | str, mr_iid: int, max_files: int = 50
    ) -> list[FileDiff]:
        pid = quote(str(project_id), safe="")
        diffs: list[FileDiff] = []
        page = 1

        while True:
            resp = await self._client.get(
                f"{self._base}/api/v4/projects/{pid}/merge_requests/{mr_iid}/diffs",
                params={"page": page, "per_page": 100},
            )
            resp.raise_for_status()
            page_data = resp.json()
            if not page_data:
                break

            for item in page_data:
                diffs.append(
                    FileDiff(
                        old_path=item.get("old_path", ""),
                        new_path=item.get("new_path", ""),
                        diff=item.get("diff", ""),
                        new_file=item.get("new_file", False),
                        deleted_file=item.get("deleted_file", False),
                        renamed_file=item.get("renamed_file", False),
                    )
                )
                if len(diffs) >= max_files:
                    logger.warning("Hit max_files=%d limit, stopping diff fetch", max_files)
                    return diffs

            if "X-Next-Page" not in resp.headers or not resp.headers["X-Next-Page"]:
                break
            page += 1

        return diffs

    # ------------------------------------------------------------------
    # Comments
    # ------------------------------------------------------------------

    async def post_mr_note(
        self, project_id: int | str, mr_iid: int, body: str
    ) -> None:
        pid = quote(str(project_id), safe="")
        resp = await self._client.post(
            f"{self._base}/api/v4/projects/{pid}/merge_requests/{mr_iid}/notes",
            json={"body": body},
        )
        resp.raise_for_status()
        logger.info(
            "Posted review comment to project=%s MR!%d (note id=%s)",
            project_id,
            mr_iid,
            resp.json().get("id"),
        )

    async def post_mr_discussion(
        self,
        project_id: int | str,
        mr_iid: int,
        body: str,
        position: dict | None = None,
    ) -> None:
        """Post inline comment if position is provided, otherwise a general note."""
        if position is None:
            return await self.post_mr_note(project_id, mr_iid, body)

        pid = quote(str(project_id), safe="")
        resp = await self._client.post(
            f"{self._base}/api/v4/projects/{pid}/merge_requests/{mr_iid}/discussions",
            json={"body": body, "position": position},
        )
        resp.raise_for_status()
