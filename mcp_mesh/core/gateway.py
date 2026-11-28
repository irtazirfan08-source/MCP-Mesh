"""
MCP-Mesh FastAPI Gateway
Exposes standardized REST and native JSON-RPC 2.0 endpoints for agent tool discovery,
single & batch RPC execution, resilience status codes (429/503/504), and live diagnostics.
"""

import asyncio
from typing import Any, Dict, List, Optional, Union
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from mcp_mesh.core.router import MCPToolRouter

app = FastAPI(title="MCP-Mesh Gateway", version="1.3.0")
router = MCPToolRouter()


class CallToolRequest(BaseModel):
    name: str
    arguments: Optional[Dict[str, Any]] = {}


class BatchCallRequest(BaseModel):
    calls: List[CallToolRequest]


def _jsonrpc_result(req_id: Union[str, int, None], result: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _jsonrpc_error(
    req_id: Union[str, int, None], code: int, message: str, data: Any = None
) -> Dict[str, Any]:
    err: Dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


async def _dispatch_jsonrpc_item(item: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return _jsonrpc_error(None, -32600, "Invalid Request: expected JSON object.")

    is_notification = "id" not in item
    raw_id = item.get("id")
    req_id = raw_id if isinstance(raw_id, (str, int)) else None

    if item.get("jsonrpc") != "2.0":
        return None if is_notification else _jsonrpc_error(req_id, -32600, "Invalid Request: 'jsonrpc' must be '2.0'.")

    method = item.get("method")
    if not isinstance(method, str) or not method.strip():
        return None if is_notification else _jsonrpc_error(req_id, -32600, "Invalid Request: 'method' must be a non-empty string.")

    params = item.get("params")

    if method == "initialize":
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "MCP-Mesh Gateway", "version": app.version},
        }
        return None if is_notification else _jsonrpc_result(req_id, result)

    if method in ("ping", "notifications/initialized"):
        return None if is_notification else _jsonrpc_result(req_id, {})

    if method == "tools/list":
        return None if is_notification else _jsonrpc_result(req_id, {"tools": router.list_tools()})

    if method == "tools/call":
        if not isinstance(params, dict):
            return None if is_notification else _jsonrpc_error(req_id, -32602, "Invalid params: 'params' must be an object.")

        tool_name = params.get("name")
        if not isinstance(tool_name, str) or not tool_name.strip():
            return None if is_notification else _jsonrpc_error(req_id, -32602, "Invalid params: 'name' must be a non-empty string.")

        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        elif not isinstance(arguments, dict):
            return None if is_notification else _jsonrpc_error(req_id, -32602, "Invalid params: 'arguments' must be an object.")

        tool_response = await router.execute_tool(name=tool_name, arguments=arguments)
        if is_notification:
            return None

        if tool_response.get("error_type") == "not_found":
            return _jsonrpc_error(req_id, -32602, tool_response["content"][0]["text"])

        return _jsonrpc_result(req_id, tool_response)

    return None if is_notification else _jsonrpc_error(req_id, -32601, f"Method not found: '{method}'.")


@app.get("/health")
def health():
    return {"status": "active", "registered_tools_count": len(router.tools)}


@app.get("/v1/mcp/tools")
def get_tools():
    """Returns all registered tool schemas formatted for LLM function calling."""
    return {"tools": router.list_tools()}


@app.get("/v1/mcp/diagnostics")
def get_diagnostics():
    """Returns circuit breaker states, token availability, and p50/p95 latency metrics."""
    return router.get_diagnostics()


@app.post("/v1/mcp/tools/call")
async def call_tool(req: CallToolRequest):
    """Executes a single tool call requested by an LLM agent with resilience enforcement."""
    response = await router.execute_tool(name=req.name, arguments=req.arguments or {})
    if response["isError"]:
        detail = response["content"][0]["text"]
        error_type = response.get("error_type")

        if error_type == "rate_limited":
            raise HTTPException(
                status_code=429,
                detail=detail,
                headers={"Retry-After": str(response.get("retry_after_sec", 1))},
            )
        if error_type == "circuit_open":
            raise HTTPException(
                status_code=503,
                detail=detail,
                headers={"Retry-After": str(response.get("retry_after_sec", 15))},
            )
        if error_type == "timeout":
            raise HTTPException(status_code=504, detail=detail)
        raise HTTPException(status_code=400, detail=detail)

    return response


@app.post("/v1/mcp/tools/batch")
async def call_tools_batch(req: BatchCallRequest):
    """Executes multiple independent tool calls concurrently and returns per-call results."""
    if not req.calls:
        raise HTTPException(status_code=400, detail="Batch 'calls' list cannot be empty.")

    payload = [{"name": item.name, "arguments": item.arguments or {}} for item in req.calls]
    results = await router.execute_batch(payload)
    error_count = sum(1 for item in results if item["isError"])
    return {
        "total": len(results),
        "errors": error_count,
        "results": results,
    }


@app.post("/v1/mcp/rpc")
async def handle_jsonrpc(request: Request):
    """Spec-compliant MCP JSON-RPC 2.0 endpoint supporting single and batch wire payloads."""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            status_code=200,
            content=_jsonrpc_error(None, -32700, "Parse error: invalid JSON payload."),
        )

    if isinstance(payload, list):
        if not payload:
            return JSONResponse(
                status_code=200,
                content=_jsonrpc_error(None, -32600, "Invalid Request: empty batch array."),
            )
        responses = await asyncio.gather(*(_dispatch_jsonrpc_item(item) for item in payload))
        filtered = [resp for resp in responses if resp is not None]
        if not filtered:
            return Response(status_code=204)
        return JSONResponse(status_code=200, content=filtered)

    response = await _dispatch_jsonrpc_item(payload)
    if response is None:
        return Response(status_code=204)
    return JSONResponse(status_code=200, content=response)