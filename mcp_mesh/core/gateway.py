"""
MCP-Mesh FastAPI Gateway
Exposes standardized endpoints for agent tool discovery, RPC execution,
resilience status codes (429/503), and live mesh diagnostics.
"""

from typing import Any, Dict, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from mcp_mesh.core.router import MCPToolRouter

app = FastAPI(title="MCP-Mesh Gateway", version="1.1.0")
router = MCPToolRouter()


class CallToolRequest(BaseModel):
    name: str
    arguments: Optional[Dict[str, Any]] = {}


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
    """Executes a tool call requested by an LLM agent with resilience enforcement."""
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
        raise HTTPException(status_code=400, detail=detail)

    return response