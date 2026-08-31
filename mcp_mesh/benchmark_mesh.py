"""
MCP-Mesh High-Concurrency Load & Latency Benchmark.
Measures async tool dispatch throughput across concurrent agent requests.
"""
import asyncio
import os
import sys
import time

# Ensure Python resolves the root project module path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mcp_mesh.core.router import MCPToolRouter
from mcp_mesh.servers.mock_tools import sample_sandbox_runner, sample_sql_executor


async def run_benchmark(concurrency: int = 100):
  router = MCPToolRouter()

  # Register mock MCP tools
  router.register_tool(
      name="sql_analytics_query",
      description="Executes analytics SQL queries",
      input_schema={
          "type": "object",
          "properties": {"query": {"type": "string"}},
      },
      handler=sample_sql_executor,
  )
  router.register_tool(
      name="python_sandbox",
      description="Isolated code execution",
      input_schema={
          "type": "object",
          "properties": {"code": {"type": "string"}},
      },
      handler=sample_sandbox_runner,
  )

  print(
      f"\n⚡ Running MCP-Mesh benchmark with {concurrency} concurrent"
      " requests..."
  )

  t0 = time.perf_counter()
  tasks = [
      router.execute_tool(
          "sql_analytics_query",
          {"query": f"SELECT id FROM events WHERE idx={i}", "limit": 5},
      )
      for i in range(concurrency)
  ]
  results = await asyncio.gather(*tasks)
  total_time = time.perf_counter() - t0

  latencies = [r["latency_ms"] for r in results]
  avg_latency = sum(latencies) / len(latencies)
  qps = concurrency / total_time

  print("\n" + "=" * 60)
  print("           MCP-MESH BENCHMARK TELEMETRY RESULTS            ")
  print("=" * 60)
  print(f" Total Requests Completed : {concurrency}")
  print(f" Total Elapsed Time       : {total_time:.4f} s")
  print(f" Average Dispatch Latency : {avg_latency:.2f} ms")
  print(f" Throughput (QPS)         : {qps:.2f} requests/sec")
  print("=" * 60 + "\n")


if __name__ == "__main__":
  asyncio.run(run_benchmark(concurrency=150))