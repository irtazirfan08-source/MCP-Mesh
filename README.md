# MCP-Mesh: Distributed Agent Protocol Gateway & Tool Orchestrator

[![MCP-Mesh CI](https://github.com/irtazirfan08-source/MCP-Mesh/actions/workflows/ci.yml/badge.svg)](https://github.com/irtazirfan08-source/MCP-Mesh/actions/workflows/ci.yml)
![Python Version](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)
![PyTest](https://img.shields.io/badge/tests-13%20passed-brightgreen)
![Protocol](https://img.shields.io/badge/MCP-JSON--RPC%202.0-orange)

A lightweight, fault-tolerant Model Context Protocol (MCP) gateway and dynamic RPC tool router built with **FastAPI**. `MCP-Mesh` exposes both REST and native JSON-RPC 2.0 endpoints, enforces per-tool circuit breakers, token-bucket rate limits, and execution timeouts (`429`/`503`/`504`), supports concurrent batch tool fan-out, and tracks real-time p50/p95 latency telemetry.

---

## Architecture Overview

```mermaid
flowchart TD
    Agent[LLM Agent / Client] -->|REST / JSON-RPC 2.0| Gateway[FastAPI Gateway: mcp_mesh/core/gateway.py]

    subgraph API Endpoints
        Gateway -->|GET /health| Health[Node Health & Tool Count]
        Gateway -->|GET /v1/mcp/tools| Catalog[Aggregated Tool Schema Catalog]
        Gateway -->|GET /v1/mcp/diagnostics| Diag[Live Circuit, Token & Latency Telemetry]
        Gateway -->|POST /v1/mcp/tools/call| Router[MCPToolRouter: mcp_mesh/core/router.py]
        Gateway -->|POST /v1/mcp/tools/batch| Router
        Gateway -->|POST /v1/mcp/rpc| Router
    end

    subgraph Resilience & Execution Middleware
        Router --> Validator[ToolValidator: JSON Schema Check]
        Router --> Resilience[MeshResilienceManager: mcp_mesh/core/resilience.py]
        Router --> Timeout[Timeout Guard: asyncio.wait_for]
        Resilience --> Breaker[ToolCircuitBreaker: CLOSED / OPEN / HALF_OPEN]
        Resilience --> Limiter[TokenBucketRateLimiter: Burst & Refill Control]
        Resilience --> Telemetry[ToolTelemetry: p50 / p95 Latency & Error Rate]
    end

    Breaker -->|Circuit Open| HTTP503[HTTP 503 Service Unavailable + Retry-After]
    Limiter -->|Rate Exceeded| HTTP429[HTTP 429 Too Many Requests + Retry-After]
    Timeout -->|Deadline Exceeded| HTTP504[HTTP 504 Gateway Timeout]
    Timeout -->|Within Deadline| Handlers[Async & Sync Tool Handlers: mcp_mesh/servers/]