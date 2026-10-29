"""Local config paths and helpers."""
from __future__ import annotations

import os
from pathlib import Path

APP_DIR = Path(os.getenv("EMAIL_ASSISTANT_HOME", str(Path.home() / ".email_assistant")))
DB_PATH = APP_DIR / "assistant.db"
VAULT_PATH = APP_DIR / "vault.json"
TOKEN_PATH = APP_DIR / "imap_credentials.json"

APP_DIR.mkdir(parents=True, exist_ok=True)


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default)
