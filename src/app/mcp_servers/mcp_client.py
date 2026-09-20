"""Sync MCP client over stdio (FR-3.3).

McpCalendar / McpMailer mirror the direct backend method shapes exactly, so the
retry proxies, agents and tests calling them need no changes — only the
transport differs (wire-protocol MCP tools instead of in-process calls).
A background event-loop thread bridges sync agent code to the async MCP SDK.
"""
import asyncio
import atexit
import concurrent.futures
import json
import os
import sys
import threading
from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[3])


class McpTransportError(ConnectionError):
    pass


class _McpConn:
    def __init__(self, module: str, *, backend: str = "fake", tool_timeout: float = 8.0,
                 env_extra: dict | None = None):
        self.module = module
        self.backend = backend
        self.env_extra = dict(env_extra or {})
        self.tool_timeout = tool_timeout
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self.loop.run_forever, daemon=True,
                                        name=f"mcp-{module.rsplit('.', 1)[-1]}")
        self._thread.start()
        self._session = None
        self._stdio_ctx = None
        self._sess_ctx = None
        try:
            self._run(self._connect(), timeout=30)
        except Exception as e:
            self.close()
            raise McpTransportError(f"cannot start {module}: {type(e).__name__}") from e
        atexit.register(self.close)

    def _run(self, coro, timeout):
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return fut.result(timeout=timeout)

    async def _connect(self):
        from mcp.client.stdio import StdioServerParameters, stdio_client
        from mcp import ClientSession
        env = dict(os.environ)
        env["CLOSEFUTURE_MCP_BACKEND"] = self.backend
        env.update({k: v for k, v in self.env_extra.items() if v})
        env.setdefault("PYTHONUNBUFFERED", "1")
        params = StdioServerParameters(command=sys.executable,
                                       args=["-m", self.module], env=env, cwd=ROOT)
        self._stdio_ctx = stdio_client(params)
        read, write = await self._stdio_ctx.__aenter__()
        self._sess_ctx = ClientSession(read, write)
        self._session = await self._sess_ctx.__aenter__()
        await self._session.initialize()

    def tools(self) -> list[str]:
        async def _go():
            res = await self._session.list_tools()
            return [t.name for t in res.tools]
        return self._run(_go(), timeout=self.tool_timeout)

    def call(self, tool: str, args: dict) -> dict:
        async def _go():
            try:
                res = await asyncio.wait_for(
                    self._session.call_tool(tool, args), timeout=self.tool_timeout)
            except asyncio.TimeoutError as e:
                raise TimeoutError(f"MCP tool {tool} timed out") from e
            if res.isError:
                text = "".join(getattr(c, "text", "") for c in res.content or [])
                raise RuntimeError(f"MCP tool {tool} error: {text[:200]}")
            merged: dict = {}
            for c in res.content or []:
                text = getattr(c, "text", None)
                if text:
                    data = json.loads(text)
                    if isinstance(data, dict):
                        merged.update(data)
            return merged
        try:
            return self._run(_go(), timeout=self.tool_timeout + 5)
        except concurrent.futures.TimeoutError as e:
            raise TimeoutError(f"MCP tool {tool} timed out") from e

    def close(self) -> None:
        try:
            if self._session is not None:
                async def _bye():
                    try:
                        await self._sess_ctx.__aexit__(None, None, None)
                    finally:
                        await self._stdio_ctx.__aexit__(None, None, None)
                try:
                    self._run(_bye(), timeout=5)
                except Exception:
                    pass
                self._session = None
        finally:
            try:
                self.loop.call_soon_threadsafe(self.loop.stop)
            except Exception:
                pass


class McpCalendar:
    """Same shapes as Fake/RealCalendar, transported over MCP (FR-3.3)."""

    def __init__(self, *, backend: str = "fake", tool_timeout: float = 8.0,
                 env_extra: dict | None = None):
        self._conn = _McpConn("src.app.mcp_servers.calendar_server",
                              backend=backend, tool_timeout=tool_timeout,
                              env_extra=env_extra)

    def _split(self, data: dict) -> tuple[str, dict]:
        data = dict(data)
        return data.pop("result", "error"), data

    def get_availability(self, start, end) -> tuple[str, dict]:
        return self._split(self._conn.call(
            "get_availability", {"start_iso": start.isoformat(), "end_iso": end.isoformat()}))

    def create_event(self, *, event_id: str, summary: str, start, end,
                     timezone: str, attendee: str) -> tuple[str, dict]:
        return self._split(self._conn.call("create_event", {
            "event_id": event_id, "summary": summary, "start_iso": start.isoformat(),
            "end_iso": end.isoformat(), "timezone": timezone, "attendee": attendee}))

    def reschedule_event(self, event_id: str, start, end) -> tuple[str, dict]:
        return self._split(self._conn.call(
            "reschedule_event", {"event_id": event_id, "start_iso": start.isoformat(),
                                 "end_iso": end.isoformat()}))

    def cancel_event(self, event_id: str) -> tuple[str, dict]:
        return self._split(self._conn.call("cancel_event", {"event_id": event_id}))

    def get_event(self, event_id: str) -> tuple[str, dict]:
        return self._split(self._conn.call("get_event", {"event_id": event_id}))

    def close(self) -> None:
        self._conn.close()


class McpMailer:
    """Same shapes as Fake/RealGmail, transported over MCP (FR-3.3)."""

    def __init__(self, *, backend: str = "fake", tool_timeout: float = 8.0,
                 env_extra: dict | None = None):
        self._conn = _McpConn("src.app.mcp_servers.email_server",
                              backend=backend, tool_timeout=tool_timeout,
                              env_extra=env_extra)

    def _split(self, data: dict) -> tuple[str, dict]:
        data = dict(data)
        return data.pop("result", "error"), data

    def send_lead_summary(self, *, to: str, subject: str, body: str,
                          thread_id: str | None = None, in_reply_to: str | None = None,
                          idempotency_key: str) -> tuple[str, dict]:
        return self._split(self._conn.call("send_lead_summary", {
            "to": to, "subject": subject, "body": body, "thread_id": thread_id,
            "in_reply_to": in_reply_to, "idempotency_key": idempotency_key}))

    def get_send_status(self, idempotency_key: str) -> tuple[str, dict]:
        return self._split(self._conn.call(
            "get_send_status", {"idempotency_key": idempotency_key}))

    def close(self) -> None:
        self._conn.close()
