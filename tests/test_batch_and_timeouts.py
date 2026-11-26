import asyncio
import time
from fastapi.testclient import TestClient

from mcp_mesh.core.gateway import app, router
from mcp_mesh.core.router import MCPToolRouter


def test_router_timeout_records_failure_and_trips_circuit():
    async def _run():
        local_router = MCPToolRouter(default_timeout_sec=1.0)

        async def slow_worker(delay: float):
            await asyncio.sleep(delay)
            return "finished"

        local_router.register_tool(
            name="slow_tool",
            description="Simulates a stalled upstream tool",
            input_schema={"type": "object", "properties": {"delay": {"type": "number"}}},
            handler=slow_worker,
            failure_threshold=2,
            recovery_timeout_sec=30.0,
            timeout_sec=0.03,
        )

        # 1st timeout -> error_type == "timeout"
        res1 = await local_router.execute_tool("slow_tool", {"delay": 0.2})
        assert res1["isError"] is True
        assert res1["error_type"] == "timeout"
        assert res1["timeout_sec"] == 0.03

        # 2nd timeout -> trips circuit breaker to OPEN
        res2 = await local_router.execute_tool("slow_tool", {"delay": 0.2})
        assert res2["isError"] is True
        assert res2["error_type"] == "timeout"

        # 3rd call -> blocked immediately with circuit_open
        res3 = await local_router.execute_tool("slow_tool", {"delay": 0.01})
        assert res3["isError"] is True
        assert res3["error_type"] == "circuit_open"

    asyncio.run(_run())


def test_router_batch_execution_runs_concurrently():
    async def _run():
        local_router = MCPToolRouter()

        async def fetch_branch(branch: str):
            await asyncio.sleep(0.08)
            return f"ok:{branch}"

        local_router.register_tool(
            name="fetch_branch",
            description="Fetches branch telemetry",
            input_schema={"type": "object", "properties": {"branch": {"type": "string"}}},
            handler=fetch_branch,
        )

        t0 = time.perf_counter()
        results = await local_router.execute_batch([
            {"name": "fetch_branch", "arguments": {"branch": "dhaka"}},
            {"name": "fetch_branch", "arguments": {"branch": "chittagong"}},
        ])
        elapsed = time.perf_counter() - t0

        assert len(results) == 2
        assert all(r["isError"] is False for r in results)
        assert results[0]["tool"] == "fetch_branch"
        assert results[0]["content"][0]["text"] == "ok:dhaka"
        assert results[1]["content"][0]["text"] == "ok:chittagong"
        # Concurrent execution completes well below sequential 0.16s sum
        assert elapsed < 0.15

    asyncio.run(_run())


def test_gateway_http_504_and_batch_endpoint():
    async def hanging_tool():
        await asyncio.sleep(0.2)
        return "never"

    router.register_tool(
        name="gw_hanging_tool",
        description="Gateway timeout test tool",
        input_schema={"type": "object", "properties": {}},
        handler=hanging_tool,
        failure_threshold=5,
        timeout_sec=0.03,
    )
    router.register_tool(
        name="gw_fast_echo",
        description="Fast echo tool",
        input_schema={"type": "object", "properties": {"msg": {"type": "string"}}},
        handler=lambda msg: f"echo:{msg}",
    )

    client = TestClient(app)

    # Single call timeout -> HTTP 504
    timeout_resp = client.post(
        "/v1/mcp/tools/call",
        json={"name": "gw_hanging_tool", "arguments": {}},
    )
    assert timeout_resp.status_code == 504
    assert "timed out" in timeout_resp.json()["detail"]

    # Batch call with mixed fast success + missing tool error -> HTTP 200 with per-item status
    batch_resp = client.post(
        "/v1/mcp/tools/batch",
        json={
            "calls": [
                {"name": "gw_fast_echo", "arguments": {"msg": "ping"}},
                {"name": "nonexistent_tool", "arguments": {}},
            ]
        },
    )
    assert batch_resp.status_code == 200
    body = batch_resp.json()
    assert body["total"] == 2
    assert body["errors"] == 1
    assert body["results"][0]["isError"] is False
    assert body["results"][0]["content"][0]["text"] == "echo:ping"
    assert body["results"][1]["isError"] is True
    assert body["results"][1]["error_type"] == "not_found"