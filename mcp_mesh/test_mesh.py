"""End-to-end verification script for MCP-Mesh tool aggregation and execution."""

import asyncio
import os
import sys

# Ensure Python can resolve the mcp_mesh package path
sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
)

from mcp_mesh.core.router import MCPToolRouter
from mcp_mesh.servers.mock_tools import (
    sample_sandbox_runner,
    sample_sql_executor,
)


async def main():
  print("\n=======================================================")
  print("   MCP-MESH: AGENT TOOL ROUTING ENGINE VERIFICATION   ")
  print("=======================================================\n")

  router = MCPToolRouter()

  # Register tools
  router.register_tool(
      name="sql_analytics_query",
      description="Executes read-only SQL queries on analytics warehouse",
      input_schema={
          "type": "object",
          "properties": {"query": {"type": "string"}},
          "required": ["query"],
      },
      handler=sample_sql_executor,
  )
  router.register_tool(
      name="python_sandbox",
      description="Executes Python scripts in an isolated container",
      input_schema={
          "type": "object",
          "properties": {"code": {"type": "string"}},
          "required": ["code"],
      },
      handler=sample_sandbox_runner,
  )

  print(
      f"[+] Successfully aggregated {len(router.list_tools())} MCP tool schemas"
      " into mesh catalog.\n"
  )

  # Test Executions
  print("[*] Dispatching tool call 1: 'sql_analytics_query'...")
  res1 = await router.execute_tool(
      "sql_analytics_query",
      {"query": "SELECT * FROM telemetry_events", "limit": 10},
  )
  print(f"    -> Output: {res1['content'][0]['text']}")
  print(f"    -> Latency: {res1['latency_ms']} ms\n")

  print("[*] Dispatching tool call 2: 'python_sandbox'...")
  res2 = await router.execute_tool(
      "python_sandbox", {"code": "print('MCP-Mesh Active')"}
  )
  print(f"    -> Output: {res2['content'][0]['text']}")
  print(f"    -> Latency: {res2['latency_ms']} ms\n")

  print("[✔] All MCP-Mesh tool invocations completed successfully!\n")


if __name__ == "__main__":
  asyncio.run(main())