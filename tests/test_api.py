from fastapi.testclient import TestClient

from app import db
from app.api.main import app


def test_health_and_sites():
    with TestClient(app) as client:
        h = client.get("/health").json()
        assert h["status"] == "ok" and h["llm"] == "mock" and h["github"] == "dry-run"
        sites = client.get("/sites").json()
        assert any(s["site"] == "demo-orders-api" for s in sites)


def test_incident_endpoints_and_conflicts():
    with TestClient(app) as client:
        iid = db.create_incident_if_none_open("demo-orders-api", "test incident", {})
        assert client.get("/incidents").json()[0]["id"] == iid
        detail = client.get(f"/incidents/{iid}").json()
        assert detail["pending_action"] is None and detail["events"][0]["event_type"] == "OPENED"
        assert client.post(f"/incidents/{iid}/approve").status_code == 409  # not awaiting approval
        assert client.get("/incidents/9999").status_code == 404
