"""Provider Circuit Breaker & Telemetry (v6.24.0).

Protects the entire price & candle failover chain from cascading slowness:
  * CLOSED    — healthy provider, requests allowed.
  * OPEN      — after `FAIL_THRESHOLD` (3) consecutive failures, provider is
                tripped for `OPEN_SECONDS` (120s) so failover switches instantly
                without waiting on repeated 15s timeouts.
  * HALF_OPEN — after cooldown expires, a single probe request is allowed;
                success resets to CLOSED, failure immediately re-trips OPEN.

Never exposed to regular users — telemetry is Admin-only.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

FAIL_THRESHOLD = 3
OPEN_SECONDS = 120.0
MAX_OPEN_SECONDS = 480.0


@dataclass
class _ProviderState:
    successes: int = 0
    failures: int = 0
    consec_failures: int = 0
    trips: int = 0
    open_until: float = 0.0
    half_open_probe: bool = False
    ema_latency_ms: float = 0.0
    last_ok_at: float = 0.0
    last_err_at: float = 0.0
    last_err: str = ""


_LOCK = threading.Lock()
_STATES: dict[str, _ProviderState] = {}


def _get(provider: str) -> _ProviderState:
    key = (provider or "unknown").lower()
    st = _STATES.get(key)
    if st is None:
        st = _ProviderState()
        _STATES[key] = st
    return st


def reset() -> None:
    """Clear all circuit breaker state (used in unit tests)."""
    with _LOCK:
        _STATES.clear()


def is_open(provider: str, now: float | None = None) -> bool:
    """True when the provider's circuit is OPEN and should be skipped."""
    t = time.time() if now is None else float(now)
    with _LOCK:
        st = _STATES.get((provider or "unknown").lower())
        if not st or st.open_until <= 0.0:
            return False
        if t < st.open_until:
            return True
        # Cooldown elapsed -> allow a single HALF_OPEN probe
        if not st.half_open_probe:
            st.half_open_probe = True
            return False
        return True


def record_success(provider: str, latency_ms: float = 0.0, now: float | None = None) -> None:
    t = time.time() if now is None else float(now)
    with _LOCK:
        st = _get(provider)
        st.successes += 1
        st.consec_failures = 0
        st.open_until = 0.0
        st.half_open_probe = False
        st.last_ok_at = t
        if latency_ms > 0:
            st.ema_latency_ms = (
                latency_ms if st.ema_latency_ms <= 0
                else (0.75 * st.ema_latency_ms + 0.25 * latency_ms)
            )


def record_failure(provider: str, err: str = "", now: float | None = None) -> bool:
    """Record a failure. Returns True if this failure tripped the circuit OPEN."""
    t = time.time() if now is None else float(now)
    tripped = False
    with _LOCK:
        st = _get(provider)
        st.failures += 1
        st.consec_failures += 1
        st.last_err_at = t
        st.last_err = (err or "")[:120]
        if st.half_open_probe or st.consec_failures >= FAIL_THRESHOLD:
            st.trips += 1
            st.half_open_probe = False
            backoff = min(MAX_OPEN_SECONDS, OPEN_SECONDS * min(st.trips, 4))
            st.open_until = t + backoff
            tripped = True
    return tripped


def state_of(provider: str, now: float | None = None) -> str:
    t = time.time() if now is None else float(now)
    with _LOCK:
        st = _STATES.get((provider or "unknown").lower())
        if not st or st.open_until <= 0.0:
            return "CLOSED"
        if t < st.open_until:
            return "OPEN"
        return "HALF_OPEN"


def health_snapshot(now: float | None = None) -> dict[str, dict]:
    """Thread-safe snapshot for Admin System Health diagnostics."""
    t = time.time() if now is None else float(now)
    out: dict[str, dict] = {}
    with _LOCK:
        for name, st in sorted(_STATES.items()):
            if st.open_until <= 0.0:
                status = "CLOSED"
                rem = 0
            elif t < st.open_until:
                status = "OPEN"
                rem = int(max(1, st.open_until - t))
            else:
                status = "HALF_OPEN"
                rem = 0
            out[name] = {
                "state": status,
                "cooldown_remaining_s": rem,
                "successes": st.successes,
                "failures": st.failures,
                "consec_failures": st.consec_failures,
                "trips": st.trips,
                "avg_latency_ms": round(st.ema_latency_ms, 1),
                "last_err": st.last_err,
            }
    return out
