"""
Prometheus metrics for gitlab-reviewer.

All metrics are registered in a dedicated CollectorRegistry so they don't
conflict with pytest's default registry during tests.
"""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# Use a dedicated registry (avoids test isolation issues with the global one)
REGISTRY = CollectorRegistry(auto_describe=True)

# ── Review results ───────────────────────────────────────────────────────────
reviews_total = Counter(
    "glr_reviews_total",
    "Total reviews by status",
    ["status"],
    registry=REGISTRY,
)

# ── Inline comments ──────────────────────────────────────────────────────────
inline_comments_total = Counter(
    "glr_inline_comments_total",
    "Total inline GitLab Discussion comments posted",
    registry=REGISTRY,
)

# ── Auto-approvals ───────────────────────────────────────────────────────────
auto_approvals_total = Counter(
    "glr_auto_approvals_total",
    "Total MRs auto-approved by gitlab-reviewer",
    registry=REGISTRY,
)

# ── LLM ─────────────────────────────────────────────────────────────────────
llm_duration_seconds = Histogram(
    "glr_llm_duration_seconds",
    "LLM response time in seconds",
    buckets=[5, 15, 30, 60, 120, 240, 300, 480],
    registry=REGISTRY,
)

llm_errors_total = Counter(
    "glr_llm_errors_total",
    "LLM call errors",
    registry=REGISTRY,
)

# ── Queue ────────────────────────────────────────────────────────────────────
queue_pending = Gauge(
    "glr_queue_pending",
    "Current number of pending review jobs",
    registry=REGISTRY,
)

queue_active = Gauge(
    "glr_queue_active",
    "Current number of actively processing review jobs",
    registry=REGISTRY,
)

queue_enqueued_total = Counter(
    "glr_queue_enqueued_total",
    "Total jobs enqueued",
    registry=REGISTRY,
)

queue_rejected_total = Counter(
    "glr_queue_rejected_total",
    "Total jobs rejected (queue full or deduped)",
    registry=REGISTRY,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def record_review(status: str, inline_count: int = 0, auto_approved: bool = False) -> None:
    """Call after a review completes (any status)."""
    reviews_total.labels(status=status).inc()
    if inline_count > 0:
        inline_comments_total.inc(inline_count)
    if auto_approved:
        auto_approvals_total.inc()


def render_metrics() -> tuple[bytes, str]:
    """Return (body_bytes, content_type) for the /metrics endpoint."""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
