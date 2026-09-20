"""Idle + away sweeper + outbox drain (FR-3.7, FR-6.4). Via /internal/sweep."""
import time as _time


def sweep_idle(store, leadstore, *, idle_minutes: int, mailer, sales_inbox: str,
               away_grace_minutes: int = 3) -> list[str]:
    """Idle >= threshold OR tab-closed (away) past its grace -> INCOMPLETE summary.

    Presence beacons set state['away_since']; a fresh visitor message clears it.
    Ended sessions with a summary are skipped (deduped by content hash anyway).
    """
    now = _time.time()
    swept = []
    # 0 = every candidate; each session is judged below (idle threshold or away grace).
    for item in store.idle_sessions(0):
        sid = item["session_id"]
        if leadstore.summaries.get(sid) and not getattr(store, "_dirty_since_summary", False):
            continue
        idle_ok = now - item.get("last_activity", 0) >= idle_minutes * 60
        away_since = (item.get("state") or {}).get("away_since")
        away_ok = bool(away_since) and now - away_since >= away_grace_minutes * 60
        if not (idle_ok or away_ok):
            continue
        msgs = [{"role": m["role"], "content": m["content"]}
                for m in store.get_messages(sid)]
        if not any(m["role"] == "visitor" for m in msgs):
            continue
        leadstore.trigger(session_id=sid, messages=msgs, booking=item["state"].get("booking"),
                          incomplete=True, mailer=mailer, sales_inbox=sales_inbox)
        store.mark_abandoned(sid)
        swept.append(sid)
    return swept
