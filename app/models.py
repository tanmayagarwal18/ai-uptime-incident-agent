"""Pydantic schemas shared across the monitor, agent, API and dashboard."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class HealthStatus(StrEnum):
    UP = "UP"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"


class IncidentStatus(StrEnum):
    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    TICKET_CREATED = "TICKET_CREATED"
    RESOLVED = "RESOLVED"
    REJECTED = "REJECTED"


ErrorType = Literal["timeout", "connection_error", "dns_error", "ssl_error", "http_error"]
Severity = Literal["SEV1", "SEV2", "SEV3", "SEV4"]

SEVERITY_CRITERIA: dict[str, str] = {
    "SEV1": "Critical: complete outage of a critical service (connection refused, DNS failure, or every request failing with 5xx).",
    "SEV2": "Major: a service is down or returning server errors, or a critical service fails intermittently.",
    "SEV3": "Moderate: service available but degraded (latency above threshold, intermittent errors on a non-critical service).",
    "SEV4": "Minor: transient or cosmetic issue with little or no user impact.",
}


class CheckResult(BaseModel):
    """Outcome of one HTTP health check."""

    site: str
    url: str
    timestamp: str
    status: HealthStatus
    status_code: int | None = None
    response_time_ms: float | None = None
    error_type: ErrorType | None = None
    error_message: str | None = None
    response_size: int | None = None

    @property
    def is_failure(self) -> bool:
        return self.status != HealthStatus.UP


class ProbableCause(BaseModel):
    """One candidate root cause with the evidence that supports it."""

    cause: str = Field(description="Short name of the probable root cause")
    confidence: float = Field(description="Confidence between 0 and 1")
    evidence: list[str] = Field(description="Specific observations from the diagnostic tools that support it")


class Diagnosis(BaseModel):
    """Structured output of the agent's analysis step."""

    probable_causes: list[ProbableCause] = Field(description="Ranked list, most likely first")
    severity: Severity = Field(description="SEV1 (critical) to SEV4 (minor)")
    severity_reason: str = Field(description="Why this severity was chosen, referencing the criteria")
    impact_summary: str = Field(description="One or two sentences on user/business impact")
    recommended_actions: list[str] = Field(description="Ordered corrective actions, most urgent first")
    confidence: float = Field(description="Overall confidence in the diagnosis, 0 to 1")
