"""Shared dataclasses."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class EmailMessage:
    uid: int
    thread_key: str = ""
    subject: str = ""
    sender: str = ""
    recipients: str = ""
    date_utc: datetime = field(default_factory=datetime.utcnow)
    body: str = ""
    folder: str = "INBOX"


@dataclass
class ThreadChunk:
    id: str
    thread_key: str
    subject: str
    from_msg_uids: list[int] = field(default_factory=list)
    text: str = ""
    n_messages: int = 0
    last_date: datetime | None = None
    metadata: dict[str, str] = field(default_factory=dict)
