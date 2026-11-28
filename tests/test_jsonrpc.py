from fastapi.testclient import TestClient

from mcp_mesh.core.gateway import app, router


def _register_jsonrpc_demo_tools():
    router.register_tool(
        name="rpc_add",
        description="Adds two integers",
        input_schema={
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        },
        handler=lambda a, b: a + b,
    )


def test_jsonrpc_initialize_and_tools_list():
    _register_jsonrpc_demo_tools()
    client = TestClient(app)

    init_resp = client.post(
        "/v1/mcp/rpc",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
        },
    )
    assert init_resp.status_code == 200
    init_body = init_resp.json()
    assert init_body["jsonrpc"] == "2.0"
    assert init_body["id"] == 1
    assert init_body["result"]["serverInfo"]["version"] == "1.3.0"
    assert "tools" in init_body["result"]["capabilities"]

    list_resp = client.post(
        "/v1/mcp/rpc",
        json={"jsonrpc": "2.0", "id": "req-2", "method": "tools/list"},
    )
    assert list_resp.status_code == 200
    list_body = list_resp.json()
    assert list_body["id"] == "req-2"
    tool_names = [t["name"] for t in list_body["result"]["tools"]]
    assert "rpc_add" in tool_names


def test_jsonrpc_tools_call_and_error_codes():
    _register_jsonrpc_demo_tools()
    client = TestClient(app)

    # Valid tools/call
    call_resp = client.post(
        "/v1/mcp/rpc",
        json={
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {"name": "rpc_add", "arguments": {"a": 7, "b": 5}},
        },
    )
    assert call_resp.status_code == 200
    call_body = call_resp.json()
    assert call_body["id"] == 10
    assert call_body["result"]["isError"] is False
    assert call_body["result"]["content"][0]["text"] == "12"

    # Unknown tool -> JSON-RPC -32602 (Invalid params)
    unknown_tool_resp = client.post(
        "/v1/mcp/rpc",
        json={
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {"name": "missing_rpc_tool", "arguments": {}},
        },
    )
    assert unknown_tool_resp.status_code == 200
    assert unknown_tool_resp.json()["error"]["code"] == -32602

    # Unknown method -> JSON-RPC -32601 (Method not found)
    unknown_method_resp = client.post(
        "/v1/mcp/rpc",
        json={"jsonrpc": "2.0", "id": 12, "method": "resources/read"},
    )
    assert unknown_method_resp.status_code == 200
    assert unknown_method_resp.json()["error"]["code"] == -32601

    # Invalid JSON-RPC envelope -> -32600 (Invalid Request)
    invalid_env_resp = client.post(
        "/v1/mcp/rpc",
        json={"jsonrpc": "1.0", "id": 13, "method": "ping"},
    )
    assert invalid_env_resp.status_code == 200
    assert invalid_env_resp.json()["error"]["code"] == -32600


def test_jsonrpc_notification_and_batch():
    _register_jsonrpc_demo_tools()
    client = TestClient(app)

    # Notification (no 'id' field) -> HTTP 204 No Content
    notif_resp = client.post(
        "/v1/mcp/rpc",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    assert notif_resp.status_code == 204

    # Batch array with 2 requests + 1 notification -> 2 responses returned
    batch_resp = client.post(
        "/v1/mcp/rpc",
        json=[
            {
                "jsonrpc": "2.0",
                "id": 101,
                "method": "tools/call",
                "params": {"name": "rpc_add", "arguments": {"a": 2, "b": 3}},
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 102, "method": "ping"},
        ],
    )
    assert batch_resp.status_code == 200
    items = batch_resp.json()
    assert len(items) == 2
    assert items[0]["id"] == 101
    assert items[0]["result"]["content"][0]["text"] == "5"
    assert items[1]["id"] == 102
    assert items[1]["result"] == {}