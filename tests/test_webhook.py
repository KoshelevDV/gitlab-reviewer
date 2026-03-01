"""Tests for webhook handler — HMAC auth, event filtering, enqueue."""

from __future__ import annotations

from tests.conftest import make_mr_webhook_body

WEBHOOK_URL = "/webhook/gitlab"
SECRET = "test-secret"  # noqa: S105
HEADERS = {
    "X-Gitlab-Token": SECRET,
    "X-Gitlab-Event": "Merge Request Hook",
    "Content-Type": "application/json",
}


class TestAuthentication:
    async def test_valid_token_accepted(self, app):
        body = make_mr_webhook_body()
        r = await app.post(WEBHOOK_URL, json=body, headers=HEADERS)
        assert r.status_code == 200

    async def test_wrong_token_returns_401(self, app):
        body = make_mr_webhook_body()
        headers = {**HEADERS, "X-Gitlab-Token": "wrong-secret"}
        r = await app.post(WEBHOOK_URL, json=body, headers=headers)
        assert r.status_code == 401

    async def test_missing_token_returns_401(self, app):
        body = make_mr_webhook_body()
        headers = {k: v for k, v in HEADERS.items() if k != "X-Gitlab-Token"}
        r = await app.post(WEBHOOK_URL, json=body, headers=headers)
        assert r.status_code == 401


class TestEventFiltering:
    async def test_non_mr_hook_ignored(self, app):
        body = make_mr_webhook_body()
        headers = {**HEADERS, "X-Gitlab-Event": "Push Hook"}
        r = await app.post(WEBHOOK_URL, json=body, headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ignored"

    async def test_open_action_accepted(self, app):
        body = make_mr_webhook_body(action="open")
        r = await app.post(WEBHOOK_URL, json=body, headers=HEADERS)
        assert r.json()["status"] in ("accepted", "deduped_or_full")

    async def test_update_action_accepted(self, app):
        body = make_mr_webhook_body(action="update")
        r = await app.post(WEBHOOK_URL, json=body, headers=HEADERS)
        assert r.json()["status"] in ("accepted", "deduped_or_full")

    async def test_reopen_action_accepted(self, app):
        body = make_mr_webhook_body(action="reopen")
        r = await app.post(WEBHOOK_URL, json=body, headers=HEADERS)
        assert r.json()["status"] in ("accepted", "deduped_or_full")

    async def test_close_action_ignored(self, app):
        body = make_mr_webhook_body(action="close")
        r = await app.post(WEBHOOK_URL, json=body, headers=HEADERS)
        assert r.json()["status"] == "ignored"

    async def test_approved_action_ignored(self, app):
        body = make_mr_webhook_body(action="approved")
        r = await app.post(WEBHOOK_URL, json=body, headers=HEADERS)
        assert r.json()["status"] == "ignored"

    async def test_merge_action_ignored(self, app):
        body = make_mr_webhook_body(action="merge")
        r = await app.post(WEBHOOK_URL, json=body, headers=HEADERS)
        assert r.json()["status"] == "ignored"


class TestPayloadValidation:
    async def test_missing_project_id_returns_400(self, app):
        body = {
            "object_attributes": {"iid": 1, "action": "open"},
            # no "project" key
        }
        r = await app.post(WEBHOOK_URL, json=body, headers=HEADERS)
        assert r.status_code == 400

    async def test_missing_mr_iid_returns_400(self, app):
        body = {
            "project": {"id": 42},
            "object_attributes": {"action": "open"},
            # no "iid"
        }
        r = await app.post(WEBHOOK_URL, json=body, headers=HEADERS)
        assert r.status_code == 400

    async def test_response_includes_project_and_mr(self, app):
        body = make_mr_webhook_body(project_id=99, mr_iid=5)
        r = await app.post(WEBHOOK_URL, json=body, headers=HEADERS)
        data = r.json()
        assert data.get("project_id") == 99
        assert data.get("mr_iid") == 5


class TestHealthEndpoint:
    async def test_health_returns_ok(self, app):
        r = await app.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    async def test_health_includes_queue_check(self, app):
        r = await app.get("/health")
        data = r.json()
        assert "checks" in data
        assert "queue" in data["checks"]

    async def test_health_includes_db_check(self, app):
        r = await app.get("/health")
        assert r.json()["checks"]["db"]["status"] == "ok"
