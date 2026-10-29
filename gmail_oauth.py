"""Optional Gmail API (OAuth) client — an alternative to IMAP/app-password.

Why offer both? IMAP with an app password is the least-setup path; the Gmail
API gives thread IDs natively and doesn't require enabling IMAP. The API path
needs OAuth credentials (client secret JSON) from Google Cloud Console.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from models import EmailMessage

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def load_user_token_path() -> str:
    from config import TOKEN_PATH
    return str(TOKEN_PATH)


class GmailApiClient:
    def __init__(self, client_secret_path: str) -> None:
        self.client_secret_path = client_secret_path
        self.creds = None

    def authorize(self) -> None:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow

        token_path = load_user_token_path()
        creds = None
        try:
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        except Exception:
            pass
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        if not creds or not creds.valid:
            flow = InstalledAppFlow.from_client_secrets_file(
                self.client_secret_path, SCOPES)
            creds = flow.run_local_server(port=0)
        self.creds = creds
        with open(token_path, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    def fetch_recent(self, months: int = 3, progress_cb=None) -> list[EmailMessage]:
        from googleapiclient.discovery import build

        if not self.creds:
            self.authorize()
        service = build("gmail", "v1", credentials=self.creds)
        after = int((datetime.now(timezone.utc) - timedelta(days=30 * months)).timestamp())
        query = f"after:{after}"
        out: list[EmailMessage] = []
        next_token = None
        i = 0
        while True:
            resp = service.users().messages().list(
                userId="me", q=query, maxResults=100, pageToken=next_token
            ).execute()
            for meta in resp.get("messages", []):
                full = service.users().messages().get(
                    userId="me", id=meta["id"], format="full"
                ).execute()
                headers = {h["name"].lower(): h["value"]
                           for h in full["payload"].get("headers", [])}
                body = _extract_body(full["payload"])
                subject = headers.get("subject", "")
                out.append(EmailMessage(
                    uid=abs(hash(meta["id"])) % (10 ** 12),
                    thread_key=_norm_subject(subject),
                    subject=subject,
                    sender=headers.get("from", ""),
                    recipients=headers.get("to", ""),
                    date_utc=_parse_date(headers.get("date", "")),
                    body=body,
                ))
                i += 1
                if progress_cb and i % 20 == 0:
                    progress_cb(i, resp.get("resultSizeEstimate", 0))
            next_token = resp.get("nextPageToken")
            if not next_token:
                break
        return out


def _extract_body(payload) -> str:
    import base64
    import re

    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        data = base64.urlsafe_b64decode(payload["body"]["data"])
        return data.decode("utf-8", "replace")
    for part in payload.get("parts", []) or []:
        text = _extract_body(part)
        if text.strip():
            return text
    if payload.get("mimeType") == "text/html" and payload.get("body", {}).get("data"):
        html = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", "replace")
        return re.sub(r"<[^>]+>", " ", html)
    return ""


def _parse_date(s: str):
    from email.utils import parsedate_to_datetime
    try:
        dt = parsedate_to_datetime(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def _norm_subject(subject: str) -> str:
    import re
    s = re.sub(r"^((re|fwd?)\s*:\s*)+", "", (subject or ""), flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", s).strip().lower()
