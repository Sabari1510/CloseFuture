"""Email MCP server: send_lead_summary, get_send_status (FR-6.2).

Real Gmail API via OAuth refresh token (user choice); FakeEmail ($0) records
sends with thread + idempotency semantics for L0–L2. Invoked only via tool
calling — never directly from agent code (FR-6.2).
"""
import base64
from dataclasses import dataclass
from email.mime.text import MIMEText


@dataclass
class SentMail:
    to: str
    subject: str
    body: str
    thread_id: str
    message_id: str
    idempotency_key: str
    in_reply_to: str | None = None


class FakeEmail:
    def __init__(self) -> None:
        self.sent: list[SentMail] = []
        self.by_key: dict[str, SentMail] = {}
        self.failures: list[str] = []  # queued error_codes for fault tests
        self._n = 0

    def send_lead_summary(self, *, to: str, subject: str, body: str,
                          thread_id: str | None = None, in_reply_to: str | None = None,
                          idempotency_key: str) -> tuple[str, dict]:
        if self.failures:
            return "error", {"error_code": self.failures.pop(0)}
        if idempotency_key in self.by_key:  # never duplicate (FR-6.6)
            m = self.by_key[idempotency_key]
            return "ok", {"message_id": m.message_id, "thread_id": m.thread_id,
                          "duplicate": True}
        self._n += 1
        tid = thread_id or f"thread-{self._n}"
        m = SentMail(to, subject, body, tid, f"msg-{self._n}", idempotency_key, in_reply_to)
        self.sent.append(m)
        self.by_key[idempotency_key] = m
        return "ok", {"message_id": m.message_id, "thread_id": tid, "duplicate": False}

    def get_send_status(self, idempotency_key: str) -> tuple[str, dict]:
        m = self.by_key.get(idempotency_key)
        if not m:
            return "error", {"error_code": "NOT_FOUND"}
        return "ok", {"message_id": m.message_id, "thread_id": m.thread_id}


class RealGmail:
    """Gmail send from the owner's account (SETUP.md Option B). Threaded updates native."""

    def __init__(self, client_id: str, client_secret: str, refresh_token: str) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token

    def _service(self):
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        creds = Credentials(None, refresh_token=self.refresh_token,
                            token_uri="https://oauth2.googleapis.com/token",
                            client_id=self.client_id, client_secret=self.client_secret,
                            scopes=["https://www.googleapis.com/auth/gmail.send"])
        return build("gmail", "v1", credentials=creds)

    def _raw(self, to: str, subject: str, body: str,
             in_reply_to: str | None) -> dict:
        msg = MIMEText(body)
        msg["To"], msg["Subject"] = to, subject
        if in_reply_to:
            msg["In-Reply-To"], msg["References"] = in_reply_to, in_reply_to
        return {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}

    def send_lead_summary(self, *, to: str, subject: str, body: str,
                          thread_id: str | None = None, in_reply_to: str | None = None,
                          idempotency_key: str) -> tuple[str, dict]:
        svc = self._service()
        payload = self._raw(to, subject, body, in_reply_to)
        if thread_id:
            payload["threadId"] = thread_id
        res = svc.users().messages().send(userId="me", body=payload).execute()
        return "ok", {"message_id": res["id"], "thread_id": res.get("threadId", ""),
                      "duplicate": False}

    def get_send_status(self, idempotency_key: str) -> tuple[str, dict]:
        return "ok", {}  # ledger lives in email_events (FR-6.7)


# --- MCP transport (FR-3.3): same actions as wire-protocol tools. ------------
# CLOSEFUTURE_MCP_BACKEND=fake (tests) | real (deployed, Gmail OAuth env set).


def _mcp_backend():
    import os
    if os.getenv("CLOSEFUTURE_MCP_BACKEND", "fake") == "real":
        return RealGmail(os.environ["GOOGLE_CLIENT_ID"],
                         os.environ["GOOGLE_CLIENT_SECRET"],
                         os.environ["GOOGLE_REFRESH_TOKEN"])
    return FakeEmail()


try:
    from mcp.server.fastmcp import FastMCP as _FastMCP

    mcp = _FastMCP("closefuture-email")
    _BE = None

    def _be():
        global _BE
        if _BE is None:
            _BE = _mcp_backend()
        return _BE

    @mcp.tool()
    def send_lead_summary(to: str, subject: str, body: str, idempotency_key: str,
                          thread_id: str | None = None,
                          in_reply_to: str | None = None) -> dict:
        """Email the scored lead summary to sales (idempotent)."""
        status, payload = _be().send_lead_summary(
            to=to, subject=subject, body=body, thread_id=thread_id,
            in_reply_to=in_reply_to, idempotency_key=idempotency_key)
        return {"result": status, **payload}

    @mcp.tool()
    def get_send_status(idempotency_key: str) -> dict:
        """Look up a prior send by idempotency key."""
        status, payload = _be().get_send_status(idempotency_key)
        return {"result": status, **payload}
except ImportError:  # pragma: no cover - direct use without MCP installed
    mcp = None


if __name__ == "__main__":
    mcp.run()
