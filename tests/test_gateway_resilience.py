"""Integration tests for MCPToolRouter and FastAPI Gateway resilience endpoints."""

import asyncio
from fastapi.testclient import TestClient
import pytest

from mcp_mesh.core.gateway import app, router
from mcp_mesh.core.resilience import MeshResilienceManager
from mcp_mesh.core.router import MCPToolRouter


@pytest.fixture(autouse=True)
def reset_gateway_router():
    """Ensure global gateway router state is clean before and after each test."""
    router.tools.clear()
    router.execution_handlers.clear()
    router.resilience = MeshResilienceManager()
    yield
    router.tools.clear()
    router.execution_handlers.clear()
    router.resilience = MeshResilienceManager()


def test_router_rate_limit_and_circuit_breaker_enforcement():
    local_router = MCPToolRouter()

    def echo(msg: str) -> str:
        return f"echo:{msg}"

    def unstable() -> str:
        raise RuntimeError("downstream timeout")

    local_router.register_tool(
        name="echo",
        description="Echoes input text",
        input_schema={"type": "object", "properties": {"msg": {"type": "string"}}},
        handler=echo,
        rate_capacity=2.0,
        refill_rate_per_sec=0.05,
    )
    local_router.register_tool(
        name="unstable",
        description="Fails consistently",
        input_schema={"type": "object", "properties": {}},
        handler=unstable,
        failure_threshold=2,
        recovery_timeout_sec=30.0,
    )

    # Burst 2 allowed calls, 3rd call hits token-bucket rate limit
    res1 = asyncio.run(local_router.execute_tool("echo", {"msg": "a"}))
    res2 = asyncio.run(local_router.execute_tool("echo", {"msg": "b"}))
    res3 = asyncio.run(local_router.execute_tool("echo", {"msg": "c"}))

    assert res1["isError"] is False
    assert res2["isError"] is False
    assert res3["isError"] is True
    assert res3["error_type"] == "rate_limited"

    # Fail twice to trip circuit breaker, 3rd call rejected immediately with circuit_open
    fail1 = asyncio.run(local_router.execute_tool("unstable", {}))
    fail2 = asyncio.run(local_router.execute_tool("unstable", {}))
    blocked = asyncio.run(local_router.execute_tool("unstable", {}))

    assert fail1["error_type"] == "execution_error"
    assert fail2["error_type"] == "execution_error"
    assert blocked["error_type"] == "circuit_open"


def test_gateway_http_429_503_and_diagnostics_endpoint():
    client = TestClient(app)

    router.register_tool(
        name="fast_add",
        description="Adds two numbers",
        input_schema={"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}},
        handler=lambda a, b: a + b,
        rate_capacity=1.0,
        refill_rate_per_sec=0.01,
    )
    router.register_tool(
        name="broken_node",
        description="Simulates a crashing tool node",
        input_schema={"type": "object", "properties": {}},
        handler=lambda: (_ for _ in ()).throw(ValueError("node down")),
        failure_threshold=1,
        recovery_timeout_sec=60.0,
    )

    # 1. First call succeeds (200), immediate second call returns 429 Too Many Requests
    ok_resp = client.post("/v1/mcp/tools/call", json={"name": "fast_add", "arguments": {"a": 2, "b": 3}})
    assert ok_resp.status_code == 200
    assert ok_resp.json()["content"][0]["text"] == "5"

    rl_resp = client.post("/v1/mcp/tools/call", json={"name": "fast_add", "arguments": {"a": 2, "b": 3}})
    assert rl_resp.status_code == 429
    assert "Retry-After" in rl_resp.headers

    # 2. First failure returns 400 and trips circuit; second call returns 503 Service Unavailable
    err_resp = client.post("/v1/mcp/tools/call", json={"name": "broken_node", "arguments": {}})
    assert err_resp.status_code == 400

    cb_resp = client.post("/v1/mcp/tools/call", json={"name": "broken_node", "arguments": {}})
    assert cb_resp.status_code == 503
    assert "Retry-After" in cb_resp.headers

    # 3. Diagnostics endpoint reports live state and p50/p95 latencies
    diag_resp = client.get("/v1/mcp/diagnostics")
    assert diag_resp.status_code == 200
    diag = diag_resp.json()
    assert diag["monitored_tools"] == 2
    assert diag["tools"]["fast_add"]["rejected_calls"] == 1
    assert diag["tools"]["broken_node"]["circuit_state"] == "open"
    assert "p50_latency_ms" in diag["tools"]["fast_add"]
    assert "p95_latency_ms" in diag["tools"]["fast_add"]