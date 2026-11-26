"""
MCP-Mesh Tool Registry and Dynamic RPC Router.
Aggregates JSON-RPC 2.0 tool schemas, enforces circuit breakers, rate limits,
and execution timeouts, and dispatches single or batch calls with telemetry.
"""

from typing import Any, Callable, Dict, List, Optional
import asyncio
import inspect
import time

from mcp_mesh.core.resilience import (
    CircuitBreakerOpenError,
    MeshResilienceManager,
    RateLimitExceededError,
)


class MCPToolRouter:
    def __init__(
        self,
        resilience_manager: Optional[MeshResilienceManager] = None,
        default_timeout_sec: float = 30.0,
    ):
        if default_timeout_sec <= 0.0:
            raise ValueError("default_timeout_sec must be strictly positive.")
        self.tools: Dict[str, Dict[str, Any]] = {}
        self.execution_handlers: Dict[str, Callable] = {}
        self.tool_timeouts: Dict[str, float] = {}
        self.default_timeout_sec = float(default_timeout_sec)
        self.resilience = resilience_manager or MeshResilienceManager()

    def register_tool(
        self,
        name: str,
        description: str,
        input_schema: dict,
        handler: Callable,
        failure_threshold: Optional[int] = None,
        recovery_timeout_sec: Optional[float] = None,
        rate_capacity: Optional[float] = None,
        refill_rate_per_sec: Optional[float] = None,
        timeout_sec: Optional[float] = None,
    ) -> None:
        """Registers an MCP-compliant tool with schema, handler, timeout, and resilience policy."""
        effective_timeout = (
            self.default_timeout_sec if timeout_sec is None else float(timeout_sec)
        )
        if effective_timeout <= 0.0:
            raise ValueError("timeout_sec must be strictly positive.")

        self.tools[name] = {
            "name": name,
            "description": description,
            "inputSchema": input_schema,
        }
        self.execution_handlers[name] = handler
        self.tool_timeouts[name] = effective_timeout
        self.resilience.register_tool_policy(
            tool_name=name,
            failure_threshold=failure_threshold,
            recovery_timeout_sec=recovery_timeout_sec,
            rate_capacity=rate_capacity,
            refill_rate_per_sec=refill_rate_per_sec,
        )

    def list_tools(self) -> List[Dict[str, Any]]:
        """Returns aggregated tool catalog for AI Agent context injection."""
        return list(self.tools.values())

    def get_diagnostics(self) -> Dict[str, Any]:
        """Returns circuit-breaker states, token-bucket availability, and latency percentiles."""
        return self.resilience.get_mesh_diagnostics()

    async def execute_tool(self, name: str, arguments: dict) -> Dict[str, Any]:
        """Dispatches JSON-RPC tool invocation with resilience guards, timeouts, and telemetry."""
        if name not in self.execution_handlers:
            return {
                "isError": True,
                "error_type": "not_found",
                "content": [{"type": "text", "text": f"Tool '{name}' not found on MCP-Mesh."}],
                "latency_ms": 0.0,
            }

        try:
            self.resilience.before_call(name)
        except RateLimitExceededError as exc:
            return {
                "isError": True,
                "error_type": "rate_limited",
                "retry_after_sec": exc.retry_after_sec,
                "content": [{"type": "text", "text": str(exc)}],
                "latency_ms": 0.0,
            }
        except CircuitBreakerOpenError as exc:
            return {
                "isError": True,
                "error_type": "circuit_open",
                "retry_after_sec": exc.retry_after_sec,
                "content": [{"type": "text", "text": str(exc)}],
                "latency_ms": 0.0,
            }

        handler = self.execution_handlers[name]
        timeout_sec = self.tool_timeouts.get(name, self.default_timeout_sec)
        t0 = time.perf_counter()

        try:
            if inspect.iscoroutinefunction(handler):
                coro = handler(**arguments)
            else:
                coro = asyncio.to_thread(handler, **arguments)

            result = await asyncio.wait_for(coro, timeout=timeout_sec)

            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            self.resilience.after_call(name, latency_ms=elapsed_ms, success=True)
            return {
                "isError": False,
                "content": [{"type": "text", "text": str(result)}],
                "latency_ms": round(elapsed_ms, 2),
            }
        except asyncio.TimeoutError:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            self.resilience.after_call(name, latency_ms=elapsed_ms, success=False)
            return {
                "isError": True,
                "error_type": "timeout",
                "timeout_sec": timeout_sec,
                "content": [
                    {
                        "type": "text",
                        "text": f"Tool '{name}' timed out after {timeout_sec}s.",
                    }
                ],
                "latency_ms": round(elapsed_ms, 2),
            }
        except Exception as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            self.resilience.after_call(name, latency_ms=elapsed_ms, success=False)
            return {
                "isError": True,
                "error_type": "execution_error",
                "content": [{"type": "text", "text": f"Execution error: {str(e)}"}],
                "latency_ms": round(elapsed_ms, 2),
            }

    async def execute_batch(self, calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Executes multiple independent tool calls concurrently via asyncio.gather."""
        tasks = [
            self.execute_tool(name=call["name"], arguments=call.get("arguments") or {})
            for call in calls
        ]
        raw_results = await asyncio.gather(*tasks)
        return [
            {"tool": call["name"], **result}
            for call, result in zip(calls, raw_results)
        ]