from app import db
from app.config import SiteConfig


def test_sites_upsert():
    db.upsert_sites([SiteConfig(name="a", url="http://a"), SiteConfig(name="b", url="http://b", critical=True)])
    db.upsert_sites([SiteConfig(name="a", url="http://a2")])
    sites = {s["name"]: s for s in db.list_sites()}
    assert sites["a"]["url"] == "http://a2" and sites["b"]["critical"] == 1


def test_incident_json_fields_roundtrip():
    iid = db.create_incident_if_none_open("a", "down", {"checks": [1, 2]})
    db.update_incident(iid, diagnosis_json={"severity": "SEV2"}, severity="SEV2")
    inc = db.get_incident(iid)
    assert inc["trigger"] == {"checks": [1, 2]} and inc["diagnosis"]["severity"] == "SEV2"
    assert db.open_incident_for_site("a")["id"] == iid


def test_events_and_tool_runs():
    iid = db.create_incident_if_none_open("a", "down", {})
    db.add_event(iid, "TEST", "hello", {"k": 1})
    db.insert_tool_run(iid, "http_probe", {"url": "u"}, {"ok": False}, 12.5, 1)
    assert [e["event_type"] for e in db.list_events(iid)] == ["OPENED", "TEST"]
    run = db.list_tool_runs(iid)[0]
    assert run["args"] == {"url": "u"} and run["result"] == {"ok": False}


def test_latest_check_per_site():
    for site, status in (("a", "DOWN"), ("a", "UP"), ("b", "DOWN")):
        db.insert_check({"site": site, "url": "u", "timestamp": db.utcnow(), "status": status, "status_code": 200,
                         "response_time_ms": 5, "error_type": None, "error_message": None, "response_size": 0})
    latest = {c["site"]: c["status"] for c in db.latest_check_per_site()}
    assert latest == {"a": "UP", "b": "DOWN"}
