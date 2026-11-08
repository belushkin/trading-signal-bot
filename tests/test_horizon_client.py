import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from stellar_intelligence_bot.chains.stellar.client import HorizonClient


@pytest.mark.asyncio
async def test_records_paginates_from_persisted_cursor() -> None:
    calls: list[str | None] = []

    async def operations(request: web.Request) -> web.Response:
        cursor = request.query.get("cursor")
        calls.append(cursor)
        if cursor == "0":
            records = [{"paging_token": str(i)} for i in range(1, 201)]
        else:
            records = [{"paging_token": "201"}, {"paging_token": "202"}]
        return web.json_response({"_embedded": {"records": records}})

    app = web.Application()
    app.router.add_get("/operations", operations)
    server = TestServer(app)
    await server.start_server()
    try:
        async with HorizonClient(str(server.make_url("/")).rstrip("/")) as client:
            records = [
                record
                async for record in client.records("operations", cursor="0")
            ]
        assert [record["paging_token"] for record in records] == [
            str(i) for i in range(1, 203)
        ]
        assert calls == ["0", "200"]
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_initial_horizon_page_is_reordered_oldest_first() -> None:
    async def ledgers(_: web.Request) -> web.Response:
        return web.json_response(
            {"_embedded": {"records": [{"paging_token": "2"}, {"paging_token": "1"}]}}
        )

    app = web.Application()
    app.router.add_get("/ledgers", ledgers)
    server = TestServer(app)
    await server.start_server()
    try:
        async with HorizonClient(str(server.make_url("/")).rstrip("/")) as client:
            records = [record async for record in client.records("ledgers", cursor=None)]
        assert [record["paging_token"] for record in records] == ["1", "2"]
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_temporary_rate_limit_is_retried() -> None:
    attempts = 0

    async def status(_: web.Request) -> web.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return web.Response(status=429, headers={"Retry-After": "0"})
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_get("/status", status)
    server = TestServer(app)
    await server.start_server()
    try:
        async with HorizonClient(str(server.make_url("/")).rstrip("/")) as client:
            assert await client.get_json("status") == {"ok": True}
        assert attempts == 2
    finally:
        await server.close()
