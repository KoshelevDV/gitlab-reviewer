"""Tests for /api/v1/targets — CRUD for review targets."""

from __future__ import annotations

NEW_TARGET = {
    "type": "project",
    "id": "99",
    "branches": {"pattern": "main", "protected_only": False},
    "auto_approve": False,
    "prompts": {"system": []},
    "author_allowlist": [],
    "skip_authors": [],
}


class TestListTargets:
    async def test_list_returns_200(self, app):
        r = await app.get("/api/v1/targets")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    async def test_list_includes_key(self, app):
        await app.post("/api/v1/targets", json=NEW_TARGET)
        r = await app.get("/api/v1/targets")
        keys = [t["_key"] for t in r.json()]
        assert "project:99" in keys


class TestAddTarget:
    async def test_add_returns_201(self, app):
        r = await app.post("/api/v1/targets", json=NEW_TARGET)
        assert r.status_code == 201
        assert r.json()["status"] == "created"
        assert r.json()["key"] == "project:99"

    async def test_add_persists_in_list(self, app):
        await app.post("/api/v1/targets", json=NEW_TARGET)
        r = await app.get("/api/v1/targets")
        ids = [t["id"] for t in r.json()]
        assert "99" in ids

    async def test_add_duplicate_returns_409(self, app):
        await app.post("/api/v1/targets", json=NEW_TARGET)
        r = await app.post("/api/v1/targets", json=NEW_TARGET)
        assert r.status_code == 409

    async def test_add_all_type_target(self, app):
        r = await app.post(
            "/api/v1/targets",
            json={
                "type": "all",
                "id": "",
                "auto_approve": False,
                "branches": {"pattern": "*"},
                "prompts": {"system": []},
                "author_allowlist": [],
                "skip_authors": [],
            },
        )
        assert r.status_code == 201
        assert r.json()["key"] == "all:"

    async def test_add_with_branch_rules(self, app):
        r = await app.post(
            "/api/v1/targets",
            json={
                **NEW_TARGET,
                "id": "100",
                "branches": {"pattern": "main,release/*", "protected_only": True},
            },
        )
        assert r.status_code == 201
        listing = await app.get("/api/v1/targets")
        t = next(x for x in listing.json() if x["id"] == "100")
        assert t["branches"]["pattern"] == "main,release/*"
        assert t["branches"]["protected_only"] is True

    async def test_add_with_author_filters(self, app):
        r = await app.post(
            "/api/v1/targets",
            json={
                **NEW_TARGET,
                "id": "101",
                "author_allowlist": ["alice", "bob"],
                "skip_authors": ["ci-bot"],
            },
        )
        assert r.status_code == 201
        listing = await app.get("/api/v1/targets")
        t = next(x for x in listing.json() if x["id"] == "101")
        assert t["author_allowlist"] == ["alice", "bob"]
        assert t["skip_authors"] == ["ci-bot"]


class TestUpdateTarget:
    async def test_update_changes_pattern(self, app):
        await app.post("/api/v1/targets", json=NEW_TARGET)
        updated = {**NEW_TARGET, "branches": {"pattern": "release/*", "protected_only": False}}
        r = await app.put("/api/v1/targets/project:99", json=updated)
        assert r.status_code == 200
        listing = await app.get("/api/v1/targets")
        t = next(x for x in listing.json() if x["id"] == "99")
        assert t["branches"]["pattern"] == "release/*"

    async def test_update_nonexistent_returns_404(self, app):
        r = await app.put("/api/v1/targets/project:ghost", json=NEW_TARGET)
        assert r.status_code == 404

    async def test_update_auto_approve(self, app):
        await app.post("/api/v1/targets", json=NEW_TARGET)
        r = await app.put("/api/v1/targets/project:99", json={**NEW_TARGET, "auto_approve": True})
        assert r.status_code == 200
        listing = await app.get("/api/v1/targets")
        t = next(x for x in listing.json() if x["id"] == "99")
        assert t["auto_approve"] is True


class TestDeleteTarget:
    async def test_delete_removes_target(self, app):
        await app.post("/api/v1/targets", json=NEW_TARGET)
        r = await app.delete("/api/v1/targets/project:99")
        assert r.status_code == 200
        listing = await app.get("/api/v1/targets")
        ids = [t["id"] for t in listing.json()]
        assert "99" not in ids

    async def test_delete_nonexistent_returns_404(self, app):
        r = await app.delete("/api/v1/targets/project:ghost")
        assert r.status_code == 404

    async def test_delete_all_type(self, app):
        await app.post(
            "/api/v1/targets",
            json={
                "type": "all",
                "id": "",
                "auto_approve": False,
                "branches": {"pattern": "*"},
                "prompts": {"system": []},
                "author_allowlist": [],
                "skip_authors": [],
            },
        )
        r = await app.delete("/api/v1/targets/all:")
        assert r.status_code == 200
