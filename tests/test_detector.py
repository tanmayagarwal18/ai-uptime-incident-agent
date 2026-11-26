from app import db
from app.db import utcnow
from app.models import CheckResult, HealthStatus
from app.monitor.detector import evaluate


def _record(site, status=HealthStatus.DOWN, code=500):
    r = CheckResult(
        site=site.name, url=site.url, timestamp=utcnow(), status=status, status_code=code,
        response_time_ms=50, error_type="http_error" if status == HealthStatus.DOWN else None,
    )
    db.insert_check(r.model_dump(mode="json"))
    return r


def test_single_failure_does_not_open_incident(site):
    assert evaluate(site, _record(site)) is None


def test_consecutive_failures_open_one_incident(site):
    _record(site)
    incident_id = evaluate(site, _record(site))
    assert incident_id is not None
    inc = db.get_incident(incident_id)
    assert inc["status"] == "OPEN"
    assert "HTTP 500" in inc["summary"]
    assert len(inc["trigger"]["checks"]) == 2


def test_no_duplicate_while_open(site):
    _record(site)
    assert evaluate(site, _record(site)) is not None
    assert evaluate(site, _record(site)) is None
    assert len(db.list_incidents(site=site.name)) == 1


def test_recovery_in_window_resets_debounce(site):
    _record(site)
    _record(site, HealthStatus.UP, 200)
    assert evaluate(site, _record(site)) is None


def test_up_check_never_opens(site):
    assert evaluate(site, _record(site, HealthStatus.UP, 200)) is None


def test_new_incident_after_resolution(site):
    _record(site)
    first = evaluate(site, _record(site))
    db.update_incident(first, status="RESOLVED")
    _record(site)
    assert evaluate(site, _record(site)) not in (None, first)
