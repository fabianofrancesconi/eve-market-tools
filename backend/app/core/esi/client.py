"""ESI HTTP client utilities — rate-limit handling and base exceptions."""
import time


class LPError(Exception):
    """User-facing error (bad corp name, no LP store, etc.)."""


class ESIRateLimited(LPError):
    """Raised when ESI returns 420 (error-limited)."""


def check_esi_rate_limit(resp):
    """Check ESI rate-limit headers; sleep if near the limit, raise on 420."""
    if resp.status_code == 420:
        try:
            reset = int(resp.headers.get("X-ESI-Error-Limit-Reset", 30))
        except (TypeError, ValueError):
            reset = 30
        time.sleep(reset)
        raise ESIRateLimited(f"ESI error-limited; waited {reset}s.")
    remain = resp.headers.get("X-ESI-Error-Limit-Remain")
    if isinstance(remain, str) and remain.isdigit() and int(remain) < 20:
        try:
            reset = int(resp.headers.get("X-ESI-Error-Limit-Reset", 5))
        except (TypeError, ValueError):
            reset = 5
        time.sleep(min(reset, 5))
