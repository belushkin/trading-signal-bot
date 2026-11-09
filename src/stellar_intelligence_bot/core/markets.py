"""Stellar asset and monitored-pair definitions."""

from __future__ import annotations

from dataclasses import dataclass

from stellar_sdk import Asset


@dataclass(frozen=True, slots=True)
class StellarAsset:
    code: str | None = None
    issuer: str | None = None

    def __post_init__(self) -> None:
        if self.code is None and self.issuer is None:
            return
        if not self.code or not self.issuer:
            raise ValueError("Issued assets must specify both code and issuer")
        try:
            Asset(self.code, self.issuer)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Invalid Stellar asset {self.code}:{self.issuer}") from exc

    @classmethod
    def parse(cls, value: str) -> StellarAsset:
        if value.strip().lower() == "native":
            return cls()
        parts = value.split(":", maxsplit=1)
        if len(parts) != 2:
            raise ValueError(f"Expected 'native' or 'CODE:ISSUER', got {value!r}")
        return cls(code=parts[0], issuer=parts[1])

    @property
    def key(self) -> str:
        if self.code is None:
            return "native"
        return f"{self.code}:{self.issuer}"

    def horizon_params(self, prefix: str) -> dict[str, str]:
        if self.code is None:
            return {f"{prefix}_asset_type": "native"}
        sdk_asset = Asset(self.code, self.issuer or "")
        serialized = sdk_asset.to_dict()
        return {
            f"{prefix}_asset_type": str(serialized["type"]),
            f"{prefix}_asset_code": self.code,
            f"{prefix}_asset_issuer": self.issuer or "",
        }


@dataclass(frozen=True, slots=True)
class MarketPair:
    base: StellarAsset
    quote: StellarAsset

    def __post_init__(self) -> None:
        if self.base == self.quote:
            raise ValueError("A market pair must contain two different assets")

    @classmethod
    def parse(cls, value: str) -> MarketPair:
        parts = value.split("/", maxsplit=1)
        if len(parts) != 2:
            raise ValueError(f"Expected BASE/QUOTE, got {value!r}")
        return cls(StellarAsset.parse(parts[0]), StellarAsset.parse(parts[1]))

    @property
    def key(self) -> str:
        return f"{self.base.key}/{self.quote.key}"

    def horizon_params(self) -> dict[str, str]:
        return self.base.horizon_params("base") | self.quote.horizon_params("counter")

    def order_book_params(self) -> dict[str, str]:
        return self.base.horizon_params("selling") | self.quote.horizon_params("buying")
