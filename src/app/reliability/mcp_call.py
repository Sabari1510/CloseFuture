"""Sync MCP retry proxies: classify, retry retryable only, log every attempt (FR-8.2, FR-8.5).

Wraps any calendar/mailer backend with identical method shapes. Non-retryable
codes (400/401/403/404/422, SLOT_UNAVAILABLE, NOT_FOUND...) return immediately.
Retryable (TIMEOUT/RATE_LIMITED/UPSTREAM_5XX/NETWORK) retry max 2 with backoff;
FAULT env (e.g. calendar:503:2) injects failures for A8 demos. Failures come
back as AgentError-shaped dicts (FR-8.3), never raw exceptions.
"""
import time

from .error_map import ERROR_RETRYABLE

RETRYABLE = {k for k, v in ERROR_RETRYABLE.items() if v}


class _Proxy:
    target = ""
    methods: tuple = ()

    def __init__(self, inner, *, events=None, max_retries: int = 2,
                 sleeper=None) -> None:
        self.inner = inner
        self.events = events
        self.max_retries = max_retries
        self.sleeper = sleeper or time.sleep
        self.ctx = ("", "", "")
        self._fault_left: int | None = None

    def set_ctx(self, session_id: str, turn_id: str, trace_id: str) -> None:
        self.ctx = (session_id, turn_id, trace_id)

    def __getattr__(self, name: str):
        # read-through to the wrapped backend (e.g. .sent, .failures, .events)
        if name.startswith("_") or name in ("inner", "events", "ctx"):
            raise AttributeError(name)
        return getattr(object.__getattribute__(self, "inner"), name)

    def _log(self, type: str, agent: str, payload: dict) -> None:
        if self.events:
            sid, tid, tr = self.ctx
            self.events.append(sid, tid, tr, type, agent, payload)

    def _fault_code(self) -> str | None:
        import os
        raw = os.getenv("FAULT", "")
        if not raw or ":" not in raw:
            return None
        parts = raw.split(":")
        if parts[0] != self.target:
            return None
        if self._fault_left is None:
            c = parts[2] if len(parts) > 2 else "1"
            self._fault_left = 10**9 if c == "always" else int(c or 1)
        if self._fault_left > 0:
            self._fault_left -= 1
            code = parts[1] if len(parts) > 1 else "500"
            return "UPSTREAM_5XX" if code.startswith("5") else "BAD_REQUEST"
        return None

    def _call(self, agent: str, tool: str, fn, *args, **kwargs) -> tuple[str, dict]:
        attempts = 0
        while True:
            attempts += 1
            fault = self._fault_code()
            try:
                if fault:
                    status, payload = "error", {"error_code": fault}
                else:
                    status, payload = fn(*args, **kwargs)
            except TimeoutError:
                status, payload = "error", {"error_code": "TIMEOUT"}
            except ConnectionError:
                status, payload = "error", {"error_code": "NETWORK"}
            except Exception as e:  # never leak raw exceptions (FR-8.3)
                status, payload = "error", {"error_code": "UPSTREAM_5XX",
                                            "message": type(e).__name__}
            if status == "ok":
                if attempts > 1:
                    self._log("retry", agent, {"tool": tool, "outcome": "succeeded_after_N",
                                              "attempts": attempts})
                return status, payload
            code = payload.get("error_code", "UPSTREAM_5XX")
            if code not in RETRYABLE or attempts > self.max_retries:
                self._log("retry", agent, {"tool": tool, "outcome": "failed_fell_back"
                                           if code in RETRYABLE else "failed_gave_up",
                                           "attempts": attempts, "error_code": code})
                payload = {"status": "error", "error_code": code,
                           "message": payload.get("message", code),
                           "retryable": code in RETRYABLE, "agent": agent}
                return "error", payload
            self._log("retry", agent, {"tool": tool, "attempt": attempts,
                                      "error_code": code, "outcome": "retrying"})
            self.sleeper(min(2**attempts, 8))


class RetryingCalendar(_Proxy):
    target = "calendar"

    def get_availability(self, *a, **k):
        return self._call("scheduler", "get_availability", self.inner.get_availability, *a, **k)

    def create_event(self, *a, **k):
        return self._call("scheduler", "create_event", self.inner.create_event, *a, **k)

    def reschedule_event(self, *a, **k):
        return self._call("scheduler", "reschedule_event", self.inner.reschedule_event, *a, **k)

    def cancel_event(self, *a, **k):
        return self._call("scheduler", "cancel_event", self.inner.cancel_event, *a, **k)

    def get_event(self, *a, **k):
        return self._call("scheduler", "get_event", self.inner.get_event, *a, **k)


class RetryingMailer(_Proxy):
    target = "email"

    def send_lead_summary(self, *a, **k):
        return self._call("lead_summary", "send_lead_summary",
                          self.inner.send_lead_summary, *a, **k)

    def get_send_status(self, *a, **k):
        return self._call("lead_summary", "get_send_status",
                          self.inner.get_send_status, *a, **k)
