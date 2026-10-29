"""Gmail via IMAP: fetch messages from the last N months.

Thread detection: normalized subject (strips Re:/Fwd:) + participants; groups by
subject prefix. We also read References/In-Reply-To when available.

Uses only the Python standard library (imaplib, email). Requires a Gmail
*App Password* (regular passwords are rejected by Google).
"""
from __future__ import annotations

import email
import imaplib
import re
import time
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime

from models import EmailMessage

IMAP_HOST = "imap.gmail.com"

# Subject normalization for thread grouping.
_RE_PREFIX = re.compile(r"^\s*((re|fwd?|fw)(\[\d+\])?\s*:\s*)+", re.IGNORECASE)


def normalize_subject(subject: str) -> str:
    s = _RE_PREFIX.sub("", subject or "").strip().lower()
    return re.sub(r"\s+", " ", s)


class GmailClient:
    """Thin IMAP client. The password is never written to disk."""

    def __init__(self, user: str, app_password: str) -> None:
        self.user = user
        self._password = app_password
        self._conn: imaplib.IMAP4_SSL | None = None

    # -- connection ---------------------------------------------------------
    def connect(self) -> None:
        if self._conn is None:
            self._conn = imaplib.IMAP4_SSL(IMAP_HOST)
            self._conn.login(self.user, self._password)

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.logout()
            except Exception:
                pass
            self._conn = None

    def __enter__(self) -> "GmailClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- fetching -----------------------------------------------------------
    def _select(self) -> None:
        if self._conn is None:
            raise RuntimeError("Gmail is not connected.")

        status, _ = self._conn.select("INBOX", readonly=True)
        if status != "OK":
            raise RuntimeError("Could not open INBOX")

    @staticmethod
    def _decode(field_value: bytes | None) -> str:
        if field_value is None:
            return ""
        try:
            return str(make_header(decode_header(field_value.decode("utf-8", "replace"))))
        except Exception:
            return field_value.decode("utf-8", "replace")

    @staticmethod
    def _body_of(msg: email.message.Message) -> str:
        """Extract plain-text body (falls back to stripped HTML)."""
        parts = []
        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                if ctype == "text/plain" and part.get("Content-Disposition") != "attachment":
                    try:
                        parts.append(part.get_payload(decode=True).decode(
                            part.get_content_charset() or "utf-8", "replace"))
                    except Exception:
                        pass
            if not parts:
                for part in msg.walk():
                    if part.get_content_type() == "text/html" and \
                            part.get("Content-Disposition") != "attachment":
                        try:
                            html = part.get_payload(decode=True).decode(
                                part.get_content_charset() or "utf-8", "replace")
                        except Exception:
                            continue
                        parts.append(re.sub(r"<[^>]+>", " ", html))
        else:
            payload = msg.get_payload(decode=True) or b""
            charset = msg.get_content_charset() or "utf-8"
            text = payload.decode(charset, "replace")
            if msg.get_content_type() == "text/html":
                text = re.sub(r"<[^>]+>", " ", text)
            parts.append(text)
        return "\n".join(p.strip() for p in parts if p.strip())

    def fetch_recent(self, months: int = 3, progress_cb=None) -> list[EmailMessage]:
        """Fetch INBOX messages newer than `months` months."""
        self.connect()
        self._select()
        since = (datetime.now(timezone.utc) - timedelta(days=30 * months)).strftime("%d-%b-%Y")
        status, data = self._conn.search(None, f'(SINCE "{since}")')
        if status != "OK":
            return []
        ids = data[0].split()
        out: list[EmailMessage] = []
        for i, imap_id in enumerate(ids):
            status, msg_data = self._conn.fetch(imap_id, "(BODY.PEEK[])")
            if status != "OK" or not msg_data or msg_data[0] is None:
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            subject = self._decode(msg.get("Subject", "").encode("utf-8", "replace"))
            sender = parseaddr(msg.get("From", ""))[1] or msg.get("From", "")
            recipients = msg.get("To", "") or ""
            refs = msg.get("References", "") or ""
            irt = msg.get("In-Reply-To", "") or ""

            try:
                date_utc = parsedate_to_datetime(msg.get("Date"))
                if date_utc.tzinfo is None:
                    date_utc = date_utc.replace(tzinfo=timezone.utc)
            except Exception:
                date_utc = datetime.now(timezone.utc)

            # thread key: normalized subject (good-enough heuristic for Gmail)
            thread_key = normalize_subject(subject) or f"uid-{imap_id}"

            out.append(EmailMessage(
                uid=int(imap_id),
                thread_key=thread_key,
                subject=subject,
                sender=sender,
                recipients=recipients,
                date_utc=date_utc,
                body=self._body_of(msg),
            ))
            if progress_cb and i % 20 == 0:
                progress_cb(i + 1, len(ids))
            time.sleep(0.01)  # be gentle with the server
        return out

    def test_connection(self) -> bool:
        try:
            self.connect()
            return True
        except Exception:
            return False
