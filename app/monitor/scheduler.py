"""Periodic monitoring loop: check all sites concurrently, store results, open incidents, hand them to the agent."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from app import db
from app.config import SiteConfig, get_settings, load_sites
from app.models import CheckResult
from app.monitor.checker import check_site
from app.monitor.detector import evaluate

log = logging.getLogger(__name__)

IncidentHandler = Callable[[int], None]


def run_site(site: SiteConfig) -> tuple[CheckResult, int | None]:
    """Check one site, persist the result and evaluate it. Returns (result, new_incident_id)."""
    result = check_site(site)
    db.insert_check(result.model_dump(mode="json"))
    return result, evaluate(site, result)


def run_cycle(
    sites: list[SiteConfig] | None = None,
    on_incident: IncidentHandler | None = None,
    max_workers: int = 8,
) -> list[int]:
    """Run one monitoring cycle across all sites. Returns ids of newly opened incidents."""
    sites = sites if sites is not None else load_sites()
    new_incidents: list[int] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for _result, incident_id in pool.map(run_site, sites):
            if incident_id:
                new_incidents.append(incident_id)
    for incident_id in new_incidents:
        if on_incident:
            try:
                on_incident(incident_id)
            except Exception:  # noqa: BLE001 - never let one incident kill the loop
                log.exception("Incident handler failed for #%s", incident_id)
    return new_incidents


def run_forever(on_incident: IncidentHandler | None = None, after_cycle: Callable[[], None] | None = None) -> None:
    """Blocking monitoring loop (Ctrl+C to stop)."""
    settings = get_settings()
    db.init_db()
    sites = load_sites()
    db.upsert_sites(sites)
    log.info("Monitoring %d sites every %ss", len(sites), settings.check_interval_seconds)
    while True:
        started = time.monotonic()
        try:
            new = run_cycle(sites, on_incident)
            if new:
                log.warning("New incidents this cycle: %s", new)
            if after_cycle:
                after_cycle()
        except Exception:  # noqa: BLE001
            log.exception("Monitoring cycle failed")
        time.sleep(max(1.0, settings.check_interval_seconds - (time.monotonic() - started)))
