"""Continuous monitor: checks every site each interval and hands new incidents to the AI agent.

Usage:  python scripts/run_monitor.py [--once]
"""

from __future__ import annotations

import argparse
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import services  # noqa: E402
from app.agent.runner import auto_verify_open_incidents  # noqa: E402
from app.config import get_settings, setup_logging  # noqa: E402
from app.monitor.scheduler import run_cycle, run_forever  # noqa: E402

log = logging.getLogger("monitor")
_agent_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent")


def dispatch(incident_id: int) -> None:
    """Investigate in the background so slow LLM calls never delay the next monitoring cycle."""
    _agent_pool.submit(services.handle_new_incident, incident_id)


def after_cycle() -> None:
    if get_settings().auto_verify_recovery:
        resolved = auto_verify_open_incidents()
        if resolved:
            log.info("Auto-verified recovery, resolved incidents: %s", resolved)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="run a single cycle and exit")
    args = parser.parse_args()
    setup_logging()
    services.bootstrap()
    s = get_settings()
    log.info("LLM: %s | GitHub: %s | approval required: %s",
             s.llm_label,
             s.github_repo if s.github_enabled else "dry-run", s.require_human_approval)
    if args.once:
        new = run_cycle(on_incident=services.handle_new_incident)
        after_cycle()
        log.info("Cycle complete. New incidents: %s", new or "none")
        return
    try:
        run_forever(on_incident=dispatch, after_cycle=after_cycle)
    except KeyboardInterrupt:
        log.info("Monitor stopped")
    finally:
        _agent_pool.shutdown(wait=False)


if __name__ == "__main__":
    main()
