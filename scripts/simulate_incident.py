"""Run the whole incident lifecycle from the terminal (handy as a demo fallback or smoke test).

Requires the demo server:  python -m uvicorn demo.flaky_server:app --port 8001

Usage:  python scripts/simulate_incident.py --mode error_500 [--approve] [--heal]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, services  # noqa: E402
from app.agent import runner  # noqa: E402
from app.config import setup_logging  # noqa: E402


def banner(text: str) -> None:
    print(f"\n{'=' * 70}\n{text}\n{'=' * 70}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", default="error_500",
                        help="error_500 | error_503 | slow | timeout | intermittent | not_found")
    parser.add_argument("--approve", action="store_true", help="approve the GitHub ticket automatically")
    parser.add_argument("--heal", action="store_true", help="restore the server and verify recovery")
    args = parser.parse_args()
    setup_logging()
    services.bootstrap()

    if services.demo_mode() is None:
        sys.exit("Demo server is not running. Start it with: make demo-server")

    banner(f"1. Breaking the demo Orders API (mode={args.mode}) and running health checks")
    incident_id = services.simulate_incident(args.mode)
    if not incident_id:
        sys.exit("No incident opened - the failure threshold was not reached.")
    inc = db.get_incident(incident_id)
    print(f"Incident #{incident_id} [{inc['status']}] {inc['summary']}")

    banner("2. AI agent investigation trace")
    for r in db.list_tool_runs(incident_id):
        from app.reporting.report import summarize_tool_result

        print(f"  turn {r['iteration']}  {r['tool_name']:<24} {summarize_tool_result(r['tool_name'], r['result'])}")

    diag = inc["diagnosis"] or {}
    banner(f"3. Diagnosis: {diag.get('severity')} - confidence {round(diag.get('confidence', 0) * 100)}%")
    for c in diag.get("probable_causes", []):
        print(f"  - {c['cause']} ({round(c['confidence'] * 100)}%)")
    print("\nRecommended actions:")
    for i, a in enumerate(diag.get("recommended_actions", []), 1):
        print(f"  {i}. {a}")

    if runner.pending_action(incident_id) == "approval":
        if not args.approve:
            print(f"\nAwaiting approval. Approve in the dashboard, or: curl -X POST localhost:8000/incidents/{incident_id}/approve")
            return
        banner("4. Human approval -> creating GitHub issue")
        runner.submit_approval(incident_id, True, reviewer="cli")
    inc = db.get_incident(incident_id)
    print(f"Status: {inc['status']} | GitHub: {inc['github_issue_url'] or 'dry-run (payload stored)'}")

    if args.heal:
        banner("5. Fixing the service and verifying recovery")
        services.set_demo_mode("healthy")
        result = runner.verify_recovery(incident_id)
        print(f"Recovered: {result['recovered']} -> {result['message']}")
        print(f"Final status: {db.get_incident(incident_id)['status']}")


if __name__ == "__main__":
    main()
