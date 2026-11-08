"""Minimal normalized event envelope shared across chain integrations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType

type EventValue = str | int | bool | Decimal | None


class Chain(StrEnum):
    STELLAR = "stellar"
    XRPL = "xrpl"


@dataclass(frozen=True, slots=True)
class ChainEvent:
    """Chain-neutral event identity and timing, with chain-specific details."""

    event_id: str
    chain: Chain
    event_type: str
    occurred_at: datetime
    source_ref: str
    attributes: Mapping[str, EventValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.event_id.strip():
            raise ValueError("event_id must not be empty")
        if not self.event_type.strip():
            raise ValueError("event_type must not be empty")
        if not self.source_ref.strip():
            raise ValueError("source_ref must not be empty")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        object.__setattr__(self, "attributes", MappingProxyType(dict(self.attributes)))
