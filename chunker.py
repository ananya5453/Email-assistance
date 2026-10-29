"""Thread-aware chunking.

Emails are conversations, not documents. A naive fixed-size splitter breaks the
"who agreed to what" signal. Here we:

1. Group consecutive messages of the same thread (by thread_key, date order).
2. Strip quoted history ("On ... wrote:", "> ..." lines, "From:" blocks) so the
   same text is not embedded twice at different offsets.
3. Cap chunk size; if a group exceeds it, split at message boundaries, keeping
   the header (subject/participants) with every chunk for standalone context.
4. Emit one chunk per group — this is the retrieval unit.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from models import EmailMessage, ThreadChunk

MAX_CHARS = 2200          # per chunk

_QUOTED_LINE = re.compile(r"^\s{0,3}>")
_ON_WROTE = re.compile(
    r"^\s*On .{0,120} (wrote|schrieb|a écrit|scribbled|escreveu):",
    re.IGNORECASE,
)
_FROM_BLOCK = re.compile(r"^\s*(From|De|Von)\s*:", re.IGNORECASE)
_SIG_SEP = re.compile(r"^\s*--\s*$")


def _strip_quoted(text: str) -> str:
    """Remove quoted reply history; keep the newest content of each message."""
    kept: list[str] = []
    for line in text.splitlines():
        if _QUOTED_LINE.match(line) or _ON_WROTE.match(line):
            continue
        if _FROM_BLOCK.match(line) and line.strip().endswith("wrote:"):
            continue
        kept.append(line.rstrip())
    out = "\n".join(kept)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def _clean_signature(text: str) -> str:
    """Drop everything after a bare `--` signature separator."""
    if _SIG_SEP.search(text):
        return _SIG_SEP.split(text, 1)[0].strip()
    return text.strip()


def _fmt_date(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")


def _format_message(m: EmailMessage) -> str:
    body = _clean_signature(_strip_quoted(m.body))
    return f"[{_fmt_date(m.date_utc)}] {m.sender} wrote:\n{body}"


def chunk_threads(messages: list[EmailMessage]) -> list[ThreadChunk]:
    """Group consecutive same-thread messages into retrieval chunks."""
    if not messages:
        return []
    by_thread: dict[str, list[EmailMessage]] = {}
    for m in messages:
        by_thread.setdefault(m.thread_key, []).append(m)

    chunks: list[ThreadChunk] = []
    for key, msgs in by_thread.items():
        msgs.sort(key=lambda m: m.date_utc)
        subject = next((m.subject for m in reversed(msgs) if m.subject), "")
        participants = sorted({m.sender for m in msgs})
        meta = {
            "thread_key": key,
            "participants": ", ".join(participants)[:300],
            "last_date": _fmt_date(msgs[-1].date_utc),
            "subject": subject[:300],
        }
        groups: list[list[EmailMessage]] = []
        current: list[EmailMessage] = []
        size = 0
        for m in msgs:
            rendered = _format_message(m)
            if current and size + len(rendered) > MAX_CHARS:
                groups.append(current)
                current, size = [], 0
            current.append(m)
            size += len(rendered)
        if current:
            groups.append(current)

        for gi, group in enumerate(groups):
            text = "\n\n".join(_format_message(m) for m in group)
            header = (
                f"Email thread: {subject} | participants: {meta['participants']}"
                f" | last activity {meta['last_date']}"
            )
            chunks.append(ThreadChunk(
                id=f"{key}::{gi}",
                thread_key=key,
                subject=subject,
                from_msg_uids=[m.uid for m in group],
                text=f"{header}\n\n{text}",
                n_messages=len(group),
                last_date=msgs[-1].date_utc,
                metadata=meta,
            ))
    return chunks
