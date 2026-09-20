"""Summary send orchestration: dedupe, threaded updates, outbox (FR-6.6/6.7, FR-8.1)."""
from dataclasses import dataclass, field

from .lead_summary import build_content, content_hash, render_body, subject_for


@dataclass
class SummaryRow:
    session_id: str
    version: int
    content: dict
    hash: str
    thread_id: str | None = None
    last_message_id: str | None = None


@dataclass
class LeadStore:
    """Mirrors lead_summaries + email_events (immutable) + email_outbox."""
    summaries: dict[str, SummaryRow] = field(default_factory=dict)
    sent_keys: set[str] = field(default_factory=set)  # email_events idempotency keys
    outbox: list[dict] = field(default_factory=list)

    def trigger(self, *, session_id: str, messages: list[dict], booking: dict | None,
                incomplete: bool, mailer, sales_inbox: str,
                company: str = "CloseFuture") -> dict:
        """At-most-one initial send; later changes -> threaded update; same -> no-op."""
        content = build_content(session_id=session_id, messages=messages, booking=booking,
                                incomplete=incomplete, company=company)
        h = content_hash({k: v for k, v in content.items()})
        row = self.summaries.get(session_id)
        if row and row.hash == h:
            return {"mailed": False, "reason": "unchanged"}  # FR-6.6 no-op
        kind = "initial" if row is None else "update"
        version = 1 if row is None else row.version + 1
        key = f"{session_id}:{version}"
        if key in self.sent_keys:
            return {"mailed": False, "reason": "already-sent"}
        subject = subject_for(content, kind)
        body = render_body(content)
        status, res = mailer.send_lead_summary(
            to=sales_inbox, subject=subject, body=body,
            thread_id=row.thread_id if row else None,
            in_reply_to=row.last_message_id if row else None,
            idempotency_key=key)
        if status != "ok":
            self.outbox.append({"session_id": session_id, "payload": {
                "to": sales_inbox, "subject": subject, "body": body,
                "thread_id": row.thread_id if row else None,
                "in_reply_to": row.last_message_id if row else None},
                "idempotency_key": key, "attempts": 0})  # never lose a lead (FR-8.1)
            # Still persist the summary row so content isn't recomputed from scratch
            self.summaries[session_id] = SummaryRow(session_id, version, content, h,
                                                    row.thread_id if row else None,
                                                    row.last_message_id if row else None)
            return {"mailed": False, "reason": "queued", "version": version}
        self.sent_keys.add(key)  # immutable send record (FR-6.7)
        self.summaries[session_id] = SummaryRow(session_id, version, content, h,
                                                res.get("thread_id"),
                                                res.get("message_id"))
        return {"mailed": True, "kind": kind, "version": version,
                "score": content["score"], "tier": content["tier"]}

    def drain_outbox(self, mailer) -> int:
        """Outbox worker: retry queued sends with same idempotency keys."""
        sent = 0
        for item in list(self.outbox):
            status, res = mailer.send_lead_summary(idempotency_key=item["idempotency_key"],
                                                   **item["payload"])
            item["attempts"] += 1
            if status == "ok":
                self.sent_keys.add(item["idempotency_key"])
                row = self.summaries.get(item["session_id"])
                if row:
                    row.thread_id, row.last_message_id = res.get("thread_id"), res.get("message_id")
                self.outbox.remove(item)
                sent += 1
        return sent
