"""
Distributed resilience, rate-limiting, and latency telemetry for MCP-Mesh tool nodes.
Provides per-tool Circuit Breakers, Token-Bucket Rate Limiters, and percentile metrics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
import time
from typing import Any, Dict, List, Optional, Tuple


class CircuitState(str, Enum):
    """Lifecycle states for a per-tool circuit breaker."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpenError(RuntimeError):
    """Raised when a tool invocation is blocked by an OPEN circuit breaker."""

    def __init__(self, tool_name: str, retry_after_sec: float):
        self.tool_name = tool_name
        self.retry_after_sec = round(max(0.0, retry_after_sec), 3)
        super().__init__(
            f"Circuit breaker OPEN for tool '{tool_name}'. Retry after {self.retry_after_sec}s."
        )


class RateLimitExceededError(RuntimeError):
    """Raised when a tool invocation exceeds its token-bucket rate limit."""

    def __init__(self, tool_name: str, retry_after_sec: float):
        self.tool_name = tool_name
        self.retry_after_sec = round(max(0.0, retry_after_sec), 3)
        super().__init__(
            f"Rate limit exceeded for tool '{tool_name}'. Retry after {self.retry_after_sec}s."
        )


@dataclass
class ToolCircuitBreaker:
    """Three-state circuit breaker protecting MCP-Mesh from cascading tool failures."""

    failure_threshold: int = 3
    recovery_timeout_sec: float = 15.0
    half_open_max_calls: int = 1
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: Optional[float] = None
    half_open_calls: int = 0

    def __post_init__(self) -> None:
        if self.failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1.")
        if self.recovery_timeout_sec <= 0.0:
            raise ValueError("recovery_timeout_sec must be strictly positive.")
        if self.half_open_max_calls < 1:
            raise ValueError("half_open_max_calls must be at least 1.")

    def allow_request(self, now: Optional[float] = None) -> bool:
        """Determine whether an incoming RPC call is permitted under current circuit state."""
        current = time.monotonic() if now is None else float(now)

        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            if (
                self.last_failure_time is not None
                and (current - self.last_failure_time) >= self.recovery_timeout_sec
            ):
                self.state = CircuitState.HALF_OPEN
                self.half_open_calls = 1
                return True
            return False

        # HALF_OPEN: allow bounded probe calls
        if self.half_open_calls < self.half_open_max_calls:
            self.half_open_calls += 1
            return True
        return False

    def record_success(self) -> None:
        """Record a successful tool execution and close the circuit if recovering."""
        self.failure_count = 0
        self.half_open_calls = 0
        self.success_count += 1
        if self.state in (CircuitState.HALF_OPEN, CircuitState.OPEN):
            self.state = CircuitState.CLOSED

    def record_failure(self, now: Optional[float] = None) -> None:
        """Record a failed tool execution and trip to OPEN when threshold is reached."""
        self.last_failure_time = time.monotonic() if now is None else float(now)
        self.failure_count += 1

        if (
            self.state == CircuitState.HALF_OPEN
            or self.failure_count >= self.failure_threshold
        ):
            self.state = CircuitState.OPEN
            self.half_open_calls = 0

    def retry_after(self, now: Optional[float] = None) -> float:
        """Remaining cooldown seconds before an OPEN circuit transitions to HALF_OPEN."""
        if self.state != CircuitState.OPEN or self.last_failure_time is None:
            return 0.0
        current = time.monotonic() if now is None else float(now)
        return max(0.0, self.recovery_timeout_sec - (current - self.last_failure_time))


class TokenBucketRateLimiter:
    """Continuous token-bucket rate limiter for per-tool RPC throughput control."""

    def __init__(self, capacity: float = 10.0, refill_rate_per_sec: float = 5.0):
        if capacity <= 0.0:
            raise ValueError("capacity must be strictly positive.")
        if refill_rate_per_sec <= 0.0:
            raise ValueError("refill_rate_per_sec must be strictly positive.")
        self.capacity = float(capacity)
        self.refill_rate_per_sec = float(refill_rate_per_sec)
        self.tokens = float(capacity)
        self.last_refill_time = time.monotonic()

    def _refill(self, now: float) -> None:
        elapsed = max(0.0, now - self.last_refill_time)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate_per_sec)
        self.last_refill_time = now

    def consume(
        self, tokens: float = 1.0, now: Optional[float] = None
    ) -> Tuple[bool, float]:
        """Attempt to consume tokens; returns (allowed, retry_after_sec)."""
        if tokens <= 0.0:
            raise ValueError("tokens to consume must be strictly positive.")
        if tokens > self.capacity:
            raise ValueError("Requested tokens exceed bucket capacity.")

        current = time.monotonic() if now is None else float(now)
        self._refill(current)

        if self.tokens >= tokens:
            self.tokens -= tokens
            return True, 0.0

        deficit = tokens - self.tokens
        retry_after_sec = deficit / self.refill_rate_per_sec
        return False, round(retry_after_sec, 4)


@dataclass
class ToolTelemetry:
    """Tracks invocation counts, rejection rates, and p50/p95 latencies per tool."""

    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    rejected_calls: int = 0
    latencies_ms: List[float] = field(default_factory=list)

    def record_execution(self, latency_ms: float, success: bool) -> None:
        self.total_calls += 1
        if success:
            self.successful_calls += 1
        else:
            self.failed_calls += 1
        self.latencies_ms.append(max(0.0, float(latency_ms)))

    def record_rejection(self) -> None:
        self.total_calls += 1
        self.rejected_calls += 1

    def _percentile(self, q: float) -> float:
        if not self.latencies_ms:
            return 0.0
        ordered = sorted(self.latencies_ms)
        idx = max(0, min(len(ordered) - 1, math.ceil((q / 100.0) * len(ordered)) - 1))
        return round(ordered[idx], 2)

    def p50_latency_ms(self) -> float:
        return self._percentile(50.0)

    def p95_latency_ms(self) -> float:
        return self._percentile(95.0)

    def error_rate(self) -> float:
        executed = self.successful_calls + self.failed_calls
        if executed == 0:
            return 0.0
        return round(self.failed_calls / executed, 4)

    def summary(self) -> Dict[str, Any]:
        return {
            "total_calls": self.total_calls,
            "successful_calls": self.successful_calls,
            "failed_calls": self.failed_calls,
            "rejected_calls": self.rejected_calls,
            "error_rate": self.error_rate(),
            "p50_latency_ms": self.p50_latency_ms(),
            "p95_latency_ms": self.p95_latency_ms(),
        }


class MeshResilienceManager:
    """Centralized resilience coordinator managing policies across all MCP-Mesh tools."""

    def __init__(
        self,
        default_failure_threshold: int = 3,
        default_recovery_timeout_sec: float = 15.0,
        default_rate_capacity: float = 20.0,
        default_refill_rate_per_sec: float = 10.0,
    ):
        self.default_failure_threshold = default_failure_threshold
        self.default_recovery_timeout_sec = default_recovery_timeout_sec
        self.default_rate_capacity = default_rate_capacity
        self.default_refill_rate_per_sec = default_refill_rate_per_sec

        self._breakers: Dict[str, ToolCircuitBreaker] = {}
        self._limiters: Dict[str, TokenBucketRateLimiter] = {}
        self._telemetry: Dict[str, ToolTelemetry] = {}

    def register_tool_policy(
        self,
        tool_name: str,
        failure_threshold: Optional[int] = None,
        recovery_timeout_sec: Optional[float] = None,
        rate_capacity: Optional[float] = None,
        refill_rate_per_sec: Optional[float] = None,
    ) -> None:
        """Configure explicit circuit breaker and rate-limit thresholds for a tool."""
        self._breakers[tool_name] = ToolCircuitBreaker(
            failure_threshold=failure_threshold or self.default_failure_threshold,
            recovery_timeout_sec=recovery_timeout_sec or self.default_recovery_timeout_sec,
        )
        self._limiters[tool_name] = TokenBucketRateLimiter(
            capacity=rate_capacity or self.default_rate_capacity,
            refill_rate_per_sec=refill_rate_per_sec or self.default_refill_rate_per_sec,
        )
        self._telemetry.setdefault(tool_name, ToolTelemetry())

    def _ensure_tool(self, tool_name: str) -> None:
        if tool_name not in self._breakers:
            self.register_tool_policy(tool_name)

    def before_call(self, tool_name: str, now: Optional[float] = None) -> None:
        """Enforce circuit breaker and rate-limit checks prior to executing an RPC handler."""
        self._ensure_tool(tool_name)
        breaker = self._breakers[tool_name]
        limiter = self._limiters[tool_name]
        telemetry = self._telemetry[tool_name]

        if not breaker.allow_request(now=now):
            telemetry.record_rejection()
            raise CircuitBreakerOpenError(tool_name, breaker.retry_after(now=now))

        allowed, retry_after_sec = limiter.consume(tokens=1.0, now=now)
        if not allowed:
            telemetry.record_rejection()
            raise RateLimitExceededError(tool_name, retry_after_sec)

    def after_call(
        self,
        tool_name: str,
        latency_ms: float,
        success: bool,
        now: Optional[float] = None,
    ) -> None:
        """Update circuit breaker state and latency telemetry after handler completion."""
        self._ensure_tool(tool_name)
        breaker = self._breakers[tool_name]
        telemetry = self._telemetry[tool_name]

        if success:
            breaker.record_success()
        else:
            breaker.record_failure(now=now)
        telemetry.record_execution(latency_ms=latency_ms, success=success)

    def get_mesh_diagnostics(self) -> Dict[str, Any]:
        """Return aggregated health, circuit state, and latency telemetry across all tools."""
        tools_report: Dict[str, Any] = {}
        for name, telemetry in self._telemetry.items():
            breaker = self._breakers[name]
            limiter = self._limiters[name]
            tools_report[name] = {
                "circuit_state": breaker.state.value,
                "consecutive_failures": breaker.failure_count,
                "available_tokens": round(limiter.tokens, 2),
                **telemetry.summary(),
            }
        return {
            "monitored_tools": len(tools_report),
            "tools": tools_report,
        }