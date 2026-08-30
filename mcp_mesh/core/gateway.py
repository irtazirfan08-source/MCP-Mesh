"""
MCP-Mesh FastAPI Gateway
Exposes standardized endpoints for agent tool discovery and RPC execution.
"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, Optional
from mcp_mesh.core.router import MCPToolRouter

app = FastAPI(title="MCP-Mesh Gateway", version="1.0.0")
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

@app.post("/v1/mcp/tools/call")
async def call_tool(req: CallToolRequest):
    """Executes a tool call requested by an LLM agent."""
    response = await router.execute_tool(name=req.name, arguments=req.arguments or {})
    if response["isError"]:
        raise HTTPException(status_code=400, detail=response["content"][0]["text"])
    return response