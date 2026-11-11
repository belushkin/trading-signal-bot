"""Async read-only client for the public Stellar Horizon API."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping
from typing import Any

import aiohttp

from stellar_intelligence_bot.core.markets import MarketPair

logger = logging.getLogger(__name__)


MALFORMED_RECORD_KEY = "_malformed_record"


class HorizonClient:
    def __init__(
        self,
        base_url: str,
        *,
        session: aiohttp.ClientSession | None = None,
        request_timeout_seconds: float = 20,
        max_retries: int = 5,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._session = session
        self._owns_session = session is None
        self._timeout = aiohttp.ClientTimeout(total=request_timeout_seconds)
        self._max_retries = max_retries

    async def __aenter__(self) -> HorizonClient:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def get_json(
        self, path: str, params: Mapping[str, str] | None = None
    ) -> dict[str, Any]:
        if self._session is None:
            raise RuntimeError("HorizonClient must be used as an async context manager")
        url = f"{self.base_url}/{path.lstrip('/')}"
        for attempt in range(self._max_retries + 1):
            try:
                async with self._session.get(url, params=params) as response:
                    if response.status == 429 or response.status >= 500:
                        if attempt == self._max_retries:
                            response.raise_for_status()
                        retry_after = response.headers.get("Retry-After")
                        delay = (
                            min(float(retry_after), 60)
                            if retry_after is not None
                            else min(2**attempt, 30)
                        )
                        logger.warning(
                            "Horizon temporary HTTP response",
                            extra={"status": response.status, "retry_seconds": delay},
                        )
                        await asyncio.sleep(delay)
                        continue
                    response.raise_for_status()
                    payload = await response.json(content_type=None)
                    if not isinstance(payload, dict):
                        raise ValueError(f"Expected a JSON object from {path}")
                    return payload
            except (TimeoutError, aiohttp.ClientError):
                if attempt == self._max_retries:
                    raise
                delay = min(2**attempt, 30)
                logger.warning(
                    "Horizon request failed; retrying",
                    extra={"path": path, "retry_seconds": delay},
                    exc_info=True,
                )
                await asyncio.sleep(delay)
        raise RuntimeError("Horizon retry loop exited unexpectedly")

    async def records(
        self,
        path: str,
        *,
        cursor: str | None,
        params: Mapping[str, str] | None = None,
        max_pages: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        if max_pages is not None and max_pages < 1:
            raise ValueError("max_pages must be positive")
        query = dict(params or {})
        query["limit"] = "200"
        query["order"] = "asc" if cursor else "desc"
        if cursor:
            query["cursor"] = cursor
        else:
            query["order"] = "desc"

        pages = 0
        while True:
            payload = await self.get_json(path, query)
            pages += 1
            embedded = payload.get("_embedded")
            records = embedded.get("records") if isinstance(embedded, dict) else None
            if not isinstance(records, list):
                raise ValueError(f"Unexpected Horizon records payload from {path}")
            if not records:
                return

            ordered = records if cursor else list(reversed(records))
            for record in ordered:
                if not isinstance(record, dict):
                    # Let the consumer skip one bad record instead of failing the whole page.
                    yield {MALFORMED_RECORD_KEY: repr(record)[:200]}
                    continue
                yield record

            if not cursor or len(records) < 200 or (max_pages is not None and pages >= max_pages):
                return
            last_record = records[-1]
            if not isinstance(last_record, dict):
                raise ValueError(f"Horizon page on {path} ends with a malformed record")
            next_cursor = last_record.get("paging_token")
            if not isinstance(next_cursor, str) or not next_cursor:
                raise ValueError(f"Horizon record on {path} lacks paging_token")
            query["cursor"] = next_cursor

    async def order_book(self, pair: MarketPair) -> dict[str, Any]:
        params = pair.order_book_params()
        params["limit"] = "20"
        return await self.get_json("order_book", params)
