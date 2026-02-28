"""GitLab API client — fetch MR diffs, post review comments, browse resources."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)


@dataclass
class ConnectionInfo:
    ok: bool
    version: str = ""
    username: str = ""
    error: str = ""


@dataclass
class GitLabGroup:
    id: int
    name: str
    full_path: str


@dataclass
class GitLabProject:
    id: int
    name: str
    path_with_namespace: str
    default_branch: str = "main"


@dataclass
class GitLabBranch:
    name: str
    protected: bool = False
    default: bool = False


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
    # Connection test
    # ------------------------------------------------------------------

    async def test_connection(self) -> ConnectionInfo:
        try:
            ver_resp = await self._client.get(f"{self._base}/api/v4/version")
            ver_resp.raise_for_status()
            version = ver_resp.json().get("version", "unknown")

            user_resp = await self._client.get(f"{self._base}/api/v4/user")
            user_resp.raise_for_status()
            username = user_resp.json().get("username", "unknown")

            return ConnectionInfo(ok=True, version=version, username=username)
        except Exception as exc:  # noqa: BLE001
            return ConnectionInfo(ok=False, error=str(exc))

    # ------------------------------------------------------------------
    # Browse groups / projects / branches
    # ------------------------------------------------------------------

    async def list_groups(self, search: str = "", per_page: int = 50) -> list[GitLabGroup]:
        params: dict = {"per_page": per_page, "order_by": "name", "sort": "asc"}
        if search:
            params["search"] = search
        resp = await self._client.get(f"{self._base}/api/v4/groups", params=params)
        resp.raise_for_status()
        return [
            GitLabGroup(id=g["id"], name=g["name"], full_path=g["full_path"]) for g in resp.json()
        ]

    async def list_projects(self, search: str = "", per_page: int = 50) -> list[GitLabProject]:
        params: dict = {
            "per_page": per_page,
            "order_by": "name",
            "sort": "asc",
            "membership": True,
        }
        if search:
            params["search"] = search
        resp = await self._client.get(f"{self._base}/api/v4/projects", params=params)
        resp.raise_for_status()
        return [
            GitLabProject(
                id=p["id"],
                name=p["name"],
                path_with_namespace=p["path_with_namespace"],
                default_branch=p.get("default_branch") or "main",
            )
            for p in resp.json()
        ]

    async def list_branches(self, project_id: int | str, per_page: int = 100) -> list[GitLabBranch]:
        pid = quote(str(project_id), safe="")
        resp = await self._client.get(
            f"{self._base}/api/v4/projects/{pid}/repository/branches",
            params={"per_page": per_page, "order_by": "name"},
        )
        resp.raise_for_status()
        return [
            GitLabBranch(
                name=b["name"],
                protected=b.get("protected", False),
                default=b.get("default", False),
            )
            for b in resp.json()
        ]

    # ------------------------------------------------------------------
    # MR info
    # ------------------------------------------------------------------

    async def get_mr(self, project_id: int | str, mr_iid: int) -> MRInfo:
        pid = quote(str(project_id), safe="")
        resp = await self._client.get(f"{self._base}/api/v4/projects/{pid}/merge_requests/{mr_iid}")
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

    async def post_mr_note(self, project_id: int | str, mr_iid: int, body: str) -> None:
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

    async def approve_mr(self, project_id: int | str, mr_iid: int) -> bool:
        """Approve a MR. Returns True on success."""
        pid = quote(str(project_id), safe="")
        try:
            resp = await self._client.post(
                f"{self._base}/api/v4/projects/{pid}/merge_requests/{mr_iid}/approve"
            )
            resp.raise_for_status()
            logger.info("Auto-approved MR project=%s MR!%d", project_id, mr_iid)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Auto-approve failed for project=%s MR!%d: %s", project_id, mr_iid, exc)
            return False

    async def get_mr_diff_refs(
        self,
        project_id: int | str,
        mr_iid: int,
    ) -> dict[str, str] | None:
        """
        Return {base_sha, start_sha, head_sha} for the latest MR version.
        Required for posting inline diff comments with positional anchors.
        Returns None if the MR has no versions yet.
        """
        pid = quote(str(project_id), safe="")
        resp = await self._client.get(
            f"{self._base}/api/v4/projects/{pid}/merge_requests/{mr_iid}/versions",
        )
        resp.raise_for_status()
        versions = resp.json()
        if not versions:
            return None
        v = versions[0]  # latest version
        return {
            "base_sha": v.get("base_commit_sha", ""),
            "start_sha": v.get("start_commit_sha", ""),
            "head_sha": v.get("head_commit_sha", ""),
        }

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
