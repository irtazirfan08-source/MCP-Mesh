"""Unit tests for MCP-Mesh circuit breakers, token-bucket rate limiters, and telemetry."""

import pytest

from mcp_mesh.core.resilience import (
    CircuitBreakerOpenError,
    CircuitState,
    MeshResilienceManager,
    RateLimitExceededError,
    TokenBucketRateLimiter,
    ToolCircuitBreaker,
    ToolTelemetry,
)


def test_circuit_breaker_trips_and_recovers():
    cb = ToolCircuitBreaker(failure_threshold=2, recovery_timeout_sec=10.0)
    assert cb.state == CircuitState.CLOSED
    assert cb.allow_request(now=100.0) is True

    cb.record_failure(now=100.0)
    assert cb.state == CircuitState.CLOSED

    cb.record_failure(now=101.0)
    assert cb.state == CircuitState.OPEN
    assert cb.allow_request(now=105.0) is False
    assert cb.retry_after(now=105.0) == pytest.approx(6.0)

    # After recovery timeout elapses, circuit transitions to HALF_OPEN for probe
    assert cb.allow_request(now=112.0) is True
    assert cb.state == CircuitState.HALF_OPEN
    # Second concurrent probe blocked while HALF_OPEN max calls = 1
    assert cb.allow_request(now=112.1) is False

    # Successful probe closes the circuit
    cb.record_success()
    assert cb.state == CircuitState.CLOSED
    assert cb.failure_count == 0


def test_circuit_breaker_half_open_failure_reopens():
    cb = ToolCircuitBreaker(failure_threshold=1, recovery_timeout_sec=5.0)
    cb.record_failure(now=10.0)
    assert cb.state == CircuitState.OPEN

    assert cb.allow_request(now=16.0) is True
    assert cb.state == CircuitState.HALF_OPEN

    cb.record_failure(now=16.5)
    assert cb.state == CircuitState.OPEN
    assert cb.allow_request(now=17.0) is False


def test_token_bucket_rate_limiter_refill():
    limiter = TokenBucketRateLimiter(capacity=2.0, refill_rate_per_sec=1.0)
    t0 = 50.0

    assert limiter.consume(1.0, now=t0) == (True, 0.0)
    assert limiter.consume(1.0, now=t0) == (True, 0.0)

    allowed, retry_after = limiter.consume(1.0, now=t0)
    assert allowed is False
    assert retry_after == pytest.approx(1.0)

    # Advance clock by 1.5 seconds -> 1.5 tokens refilled
    assert limiter.consume(1.0, now=t0 + 1.5) == (True, 0.0)


def test_tool_telemetry_percentiles_and_error_rate():
    tel = ToolTelemetry()
    for lat in [10.0, 20.0, 30.0, 40.0, 100.0]:
        tel.record_execution(lat, success=True)
    tel.record_execution(50.0, success=False)
    tel.record_rejection()

    summary = tel.summary()
    assert summary["total_calls"] == 7
    assert summary["successful_calls"] == 5
    assert summary["failed_calls"] == 1
    assert summary["rejected_calls"] == 1
    assert summary["error_rate"] == pytest.approx(1 / 6, rel=1e-3)
    assert summary["p50_latency_ms"] == pytest.approx(30.0)
    assert summary["p95_latency_ms"] == pytest.approx(100.0)


def test_mesh_resilience_manager_end_to_end():
    manager = MeshResilienceManager()
    manager.register_tool_policy(
        tool_name="vector_search",
        failure_threshold=2,
        recovery_timeout_sec=10.0,
        rate_capacity=2.0,
        refill_rate_per_sec=1.0,
    )

    # First call succeeds
    manager.before_call("vector_search", now=1.0)
    manager.after_call("vector_search", latency_ms=12.4, success=True, now=1.01)

    # Second call bursts remaining token
    manager.before_call("vector_search", now=1.02)
    manager.after_call("vector_search", latency_ms=15.0, success=False, now=1.03)

    # Third call at same timestamp hits token-bucket rate limit
    with pytest.raises(RateLimitExceededError):
        manager.before_call("vector_search", now=1.04)

    # Advance time for token refill, then fail a second time to trip circuit breaker
    manager.before_call("vector_search", now=3.0)
    manager.after_call("vector_search", latency_ms=25.0, success=False, now=3.01)

    with pytest.raises(CircuitBreakerOpenError):
        manager.before_call("vector_search", now=4.0)

    diag = manager.get_mesh_diagnostics()
    assert diag["monitored_tools"] == 1
    tool_stats = diag["tools"]["vector_search"]
    assert tool_stats["circuit_state"] == "open"
    assert tool_stats["rejected_calls"] == 2