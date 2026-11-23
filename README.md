# MCP-Mesh: Distributed Agent Protocol Gateway & Tool Orchestrator

[![MCP-Mesh CI](https://github.com/irtazirfan08-source/MCP-Mesh/actions/workflows/ci.yml/badge.svg)](https://github.com/irtazirfan08-source/MCP-Mesh/actions/workflows/ci.yml)
![Python Version](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)
![PyTest](https://img.shields.io/badge/tests-7%20passed-brightgreen)
![Protocol](https://img.shields.io/badge/MCP-JSON--RPC%202.0-orange)

A lightweight, fault-tolerant Model Context Protocol (MCP) gateway and dynamic RPC tool router built with **FastAPI**. `MCP-Mesh` aggregates tool schemas for LLM function calling, enforces per-tool circuit breakers and token-bucket rate limits, and exposes real-time p50/p95 latency telemetry.

---

## Architecture Overview

```mermaid
flowchart TD
    Agent[LLM Agent / Client] -->|HTTP / JSON-RPC| Gateway[FastAPI Gateway: mcp_mesh/core/gateway.py]

    subgraph API Endpoints
        Gateway -->|GET /health| Health[Node Health & Tool Count]
        Gateway -->|GET /v1/mcp/tools| Catalog[Aggregated Tool Schema Catalog]
        Gateway -->|POST /v1/mcp/tools/call| Router[MCPToolRouter: mcp_mesh/core/router.py]
        Gateway -->|GET /v1/mcp/diagnostics| Diag[Live Circuit, Token & Latency Telemetry]
    end

    subgraph Resilience & Validation Middleware
        Router --> Validator[ToolValidator: JSON Schema Check]
        Router --> Resilience[MeshResilienceManager: mcp_mesh/core/resilience.py]
        Resilience --> Breaker[ToolCircuitBreaker: CLOSED / OPEN / HALF_OPEN]
        Resilience --> Limiter[TokenBucketRateLimiter: Burst & Refill Control]
        Resilience --> Telemetry[ToolTelemetry: p50 / p95 Latency & Error Rate]
    end

    Breaker -->|Circuit Open| HTTP503[HTTP 503 Service Unavailable + Retry-After]
    Limiter -->|Rate Exceeded| HTTP429[HTTP 429 Too Many Requests + Retry-After]
    Resilience -->|Permitted| Handlers[Async & Sync Tool Handlers: mcp_mesh/servers/]