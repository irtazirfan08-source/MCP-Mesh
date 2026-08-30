"""
MCP-Mesh Tool Registry and Dynamic RPC Router.
Aggregates JSON-RPC 2.0 tool schemas and routes agent execution calls.
"""
from typing import Dict, Any, Callable, List
import asyncio
import time
import inspect

class MCPToolRouter:
    def __init__(self):
        self.tools: Dict[str, Dict[str, Any]] = {}
        self.execution_handlers: Dict[str, Callable] = {}

    def register_tool(self, name: str, description: str, input_schema: dict, handler: Callable):
        """Registers an MCP-compliant tool with schema and handler."""
        self.tools[name] = {
            "name": name,
            "description": description,
            "inputSchema": input_schema
        }
        self.execution_handlers[name] = handler

    def list_tools(self) -> List[Dict[str, Any]]:
        """Returns aggregated tool catalog for AI Agent context injection."""
        return list(self.tools.values())

    async def execute_tool(self, name: str, arguments: dict) -> Dict[str, Any]:
        """Dispatches JSON-RPC tool invocation with latency telemetry."""
        if name not in self.execution_handlers:
            return {
                "isError": True,
                "content": [{"type": "text", "text": f"Tool '{name}' not found on MCP-Mesh."}],
                "latency_ms": 0.0
            }

        handler = self.execution_handlers[name]
        t0 = time.perf_counter()
        
        try:
            if inspect.iscoroutinefunction(handler):
                result = await handler(**arguments)
            else:
                result = await asyncio.to_thread(handler, **arguments)
            
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return {
                "isError": False,
                "content": [{"type": "text", "text": str(result)}],
                "latency_ms": round(elapsed_ms, 2)
            }
        except Exception as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return {
                "isError": True,
                "content": [{"type": "text", "text": f"Execution error: {str(e)}"}],
                "latency_ms": round(elapsed_ms, 2)
            }