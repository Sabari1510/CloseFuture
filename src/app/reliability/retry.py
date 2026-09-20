"""Retry wrapper with backoff+jitter + fault injection. FR-8.1, FR-8.2, FR-8.5."""
import asyncio
import os
import random
from collections.abc import Awaitable, Callable


def parse_fault() -> dict:
    """FAULT env e.g. 'calendar:503:2', 'calendar:401', 'email:503:always'."""
    raw = os.getenv("FAULT", "")
    if not raw or ":" not in raw:
        return {}
    parts = raw.split(":")
    return {"target": parts[0], "code": parts[1] if len(parts) > 1 else "",
            "count": parts[2] if len(parts) > 2 else "1"}


async def run_with_retry(
    fn: Callable[[], Awaitable[tuple[str, dict]]],
    *,
    target: str,
    max_retries: int = 2,
    log=None,
) -> tuple[str, dict, int]:
    """Run fn; fn returns (status, payload) where status is ok/error + error_code.

    Returns (final_status, payload, attempts_made). Fault injection only in tests.
    """
    fault = parse_fault()
    attempts = 0
    remaining_faults: int | None = None
    if fault.get("target") == target:
        c = fault.get("count", "1")
        remaining_faults = 10**9 if c == "always" else int(c or 1)

    while True:
        attempts += 1
        if remaining_faults is not None and remaining_faults > 0:
            remaining_faults -= 1
            status, payload = "error", {"error_code": "UPSTREAM_5XX"
                                        if fault["code"].startswith("5") else "BAD_REQUEST"}
        else:
            try:
                status, payload = await fn()
            except TimeoutError:
                status, payload = "error", {"error_code": "TIMEOUT"}
            except ConnectionError:
                status, payload = "error", {"error_code": "NETWORK"}
        code = payload.get("error_code", "") if status == "error" else ""
        retryable = code in ("TIMEOUT", "RATE_LIMITED", "UPSTREAM_5XX", "NETWORK")
        if status == "ok" or not retryable or attempts > max_retries + 1 - 1 and attempts > max_retries:
            return status, payload, attempts
        if log:
            log.append({"attempt": attempts, "error_code": code})
        await asyncio.sleep(min(2**attempts, 8) + random.uniform(0, 0.5))
