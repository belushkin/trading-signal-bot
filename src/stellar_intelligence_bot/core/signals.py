"""Structured, evidence-first signal domain types."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class Severity(StrEnum):
    INFO = "INFO"
    WATCH = "WATCH"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Confidence(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass(frozen=True, slots=True)
class Signal:
    signal_id: str
    deduplication_key: str
    event_id: str
    occurred_at: datetime
    category: str
    severity: Severity
    confidence: Confidence
    observed_facts: dict[str, Any]
    interpretation: str
    expires_at: datetime

    def __post_init__(self) -> None:
        if self.expires_at <= self.occurred_at:
            raise ValueError("Signal expiration must follow occurrence time")
