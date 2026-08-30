"""
Mock server endpoints providing sample tools for SQL querying and code sandboxing.
"""
import asyncio

async def sample_sql_executor(query: str, limit: int = 5):
    await asyncio.sleep(0.02)  # Simulate DB IO latency
    return f"Query executed: '{query}' -> Returned {limit} rows."

async def sample_sandbox_runner(code: str):
    await asyncio.sleep(0.01)  # Simulate isolated execution
    return f"Sandbox execution successful for: {code}"