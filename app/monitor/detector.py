"""Failure detection: turns a stream of check results into incidents (with debounce)."""

from __future__ import annotations

import logging

from app import db
from app.config import SiteConfig
from app.models import CheckResult

log = logging.getLogger(__name__)


def describe_failure(check: dict) -> str:
    if check["status"] == "DEGRADED":
        return f"slow response ({check['response_time_ms']:.0f} ms)"
    if check.get("error_type") == "http_error":
        return f"HTTP {check.get('status_code')}"
    return check.get("error_type") or check["status"]


def evaluate(site: SiteConfig, latest: CheckResult) -> int | None:
    """Decide whether the latest stored checks warrant a new incident.

    An incident opens only after `site.failure_threshold` consecutive non-UP checks,
    and never while another incident for the same site is still open.
    Returns the new incident id, or None.
    """
    if not latest.is_failure:
        return None
    window = db.recent_checks(site.name, limit=site.failure_threshold)
    if len(window) < site.failure_threshold or any(c["status"] == "UP" for c in window):
        log.debug("%s: failure %d/%d - waiting", site.name, len(window), site.failure_threshold)
        return None
    if db.open_incident_for_site(site.name):
        return None

    kinds = sorted({describe_failure(c) for c in window})
    severity_hint = "DOWN" if any(c["status"] == "DOWN" for c in window) else "DEGRADED"
    summary = (
        f"{site.name} is {severity_hint}: {site.failure_threshold} consecutive failed checks "
        f"({', '.join(kinds)}) on {site.url}"
    )
    trigger = {"checks": list(reversed(window)), "site": site.model_dump()}
    incident_id = db.create_incident_if_none_open(site.name, summary, trigger)
    if incident_id:
        log.warning("Incident #%s opened: %s", incident_id, summary)
    return incident_id
