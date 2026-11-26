import json

import pytest
import responses

from app.integrations.github_client import API, GitHubClient, GitHubError

REPO = "octo/test-repo"
BASE = f"{API}/repos/{REPO}"


def test_dry_run_without_token_makes_no_requests():
    client = GitHubClient(token="", repo=REPO)
    with responses.RequestsMock() as rsps:  # any request would raise
        result = client.create_issue("t", "b", ["incident"])
        assert result["dry_run"] is True and result["payload"]["title"] == "t"
        assert client.add_comment(None, "x")["dry_run"] is True
        assert client.close_issue(None)["dry_run"] is True
        assert len(rsps.calls) == 0


@responses.activate
def test_create_issue_sends_headers_labels_and_body():
    responses.post(f"{BASE}/labels", status=422, json={"message": "already_exists"})
    responses.post(f"{BASE}/issues", status=201, json={"number": 7, "html_url": "https://github.com/octo/test-repo/issues/7"})
    client = GitHubClient(token="ghp_test", repo=REPO)
    result = client.create_issue("[SEV1] api down", "report", ["incident", "severity:SEV1"])

    assert result == {"dry_run": False, "number": 7, "html_url": "https://github.com/octo/test-repo/issues/7",
                      "payload": {"title": "[SEV1] api down", "body": "report", "labels": ["incident", "severity:SEV1"]}}
    issue_call = responses.calls[-1].request
    assert issue_call.headers["Authorization"] == "Bearer ghp_test"
    assert issue_call.headers["X-GitHub-Api-Version"] == "2022-11-28"
    assert json.loads(issue_call.body)["labels"] == ["incident", "severity:SEV1"]


@responses.activate
def test_comment_and_close():
    responses.post(f"{BASE}/issues/7/comments", status=201, json={"html_url": "c"})
    responses.patch(f"{BASE}/issues/7", status=200, json={"state": "closed"})
    client = GitHubClient(token="t", repo=REPO)
    assert client.add_comment(7, "recovered")["html_url"] == "c"
    assert client.close_issue(7)["closed"] is True
    assert json.loads(responses.calls[-1].request.body)["state"] == "closed"


@responses.activate
def test_rate_limit_raises():
    responses.post(f"{BASE}/issues", status=403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "123"})
    with pytest.raises(GitHubError, match="rate limit"):
        GitHubClient(token="t", repo=REPO).create_issue("t", "b", [])


@responses.activate
def test_auth_error_message():
    responses.post(f"{BASE}/issues", status=401, json={"message": "Bad credentials"})
    with pytest.raises(GitHubError, match="401 Bad credentials"):
        GitHubClient(token="t", repo=REPO).create_issue("t", "b", [])


def test_long_body_is_truncated():
    result = GitHubClient(token="", repo=REPO).create_issue("t", "x" * 70000, [])
    assert len(result["payload"]["body"]) < 61000


def test_from_settings_dry_run_flag(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("GITHUB_TOKEN", "abc")
    monkeypatch.setenv("GITHUB_DRY_RUN", "true")
    get_settings.cache_clear()
    assert GitHubClient.from_settings().dry_run is True
    monkeypatch.setenv("GITHUB_DRY_RUN", "false")
    get_settings.cache_clear()
    assert GitHubClient.from_settings().dry_run is False
