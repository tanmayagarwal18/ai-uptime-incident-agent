"""Minimal GitHub REST API v3 client for incident tickets (issues, comments, labels) with a dry-run mode."""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from app.config import get_settings

log = logging.getLogger(__name__)

API = "https://api.github.com"
MAX_BODY = 60000

LABEL_COLORS = {
    "incident": "d73a4a",
    "severity:SEV1": "b60205",
    "severity:SEV2": "d93f0b",
    "severity:SEV3": "fbca04",
    "severity:SEV4": "0e8a16",
}


class GitHubError(RuntimeError):
    """Raised when the GitHub API returns an error."""


class GitHubClient:
    def __init__(self, token: str, repo: str, dry_run: bool = False, session: requests.Session | None = None):
        self.repo = repo
        self.dry_run = dry_run or not token
        self.session = session or requests.Session()
        self.session.headers.update({
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ai-uptime-incident-agent",
        })
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"

    @classmethod
    def from_settings(cls) -> GitHubClient:
        s = get_settings()
        return cls(token=s.github_token.strip(), repo=s.github_repo.strip(), dry_run=not s.github_enabled)

    # ----------------------------------------------------------------- low level
    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{API}/repos/{self.repo}{path}"
        for attempt in range(3):
            resp = self.session.request(method, url, timeout=15, **kwargs)
            if resp.status_code in (403, 429) and resp.headers.get("X-RateLimit-Remaining") == "0":
                reset = resp.headers.get("X-RateLimit-Reset", "?")
                raise GitHubError(f"GitHub rate limit exceeded (resets at epoch {reset})")
            if resp.status_code >= 500 and attempt < 2:
                log.warning("GitHub %s %s -> %s, retrying", method, path, resp.status_code)
                time.sleep(1.5 * (attempt + 1))
                continue
            if resp.status_code >= 400:
                try:
                    detail = resp.json().get("message", resp.text)
                except ValueError:
                    detail = resp.text
                raise GitHubError(f"GitHub API {method} {path} failed: {resp.status_code} {detail}")
            return resp.json() if resp.content else None
        raise GitHubError(f"GitHub API {method} {path} failed after retries")

    # ----------------------------------------------------------------- operations
    def ensure_labels(self, labels: list[str]) -> None:
        if self.dry_run:
            return
        for name in labels:
            try:
                self._request("POST", "/labels", json={"name": name, "color": LABEL_COLORS.get(name, "5319e7")})
            except GitHubError as exc:
                if "422" not in str(exc):  # 422 = label already exists
                    log.warning("Could not create label %s: %s", name, exc)

    def create_issue(self, title: str, body: str, labels: list[str]) -> dict[str, Any]:
        if len(body) > MAX_BODY:
            body = body[:MAX_BODY] + "\n\n_(report truncated)_"
        payload = {"title": title, "body": body, "labels": labels}
        if self.dry_run:
            log.info("[dry-run] would create GitHub issue: %s", title)
            return {"dry_run": True, "number": None, "html_url": None, "payload": payload}
        self.ensure_labels(labels)
        data = self._request("POST", "/issues", json=payload)
        log.info("Created GitHub issue #%s %s", data["number"], data["html_url"])
        return {"dry_run": False, "number": data["number"], "html_url": data["html_url"], "payload": payload}

    def add_comment(self, number: int | None, body: str) -> dict[str, Any]:
        if self.dry_run or number is None:
            return {"dry_run": True, "body": body}
        data = self._request("POST", f"/issues/{number}/comments", json={"body": body})
        return {"dry_run": False, "html_url": data.get("html_url")}

    def close_issue(self, number: int | None) -> dict[str, Any]:
        if self.dry_run or number is None:
            return {"dry_run": True}
        self._request("PATCH", f"/issues/{number}", json={"state": "closed", "state_reason": "completed"})
        return {"dry_run": False, "closed": True}
