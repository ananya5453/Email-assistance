"""Local persistence: SQLite for messages, and a TF-IDF (hashing) vector index
over thread chunks with BM25-style scoring.

Why local TF-IDF instead of a remote embedding API? Your email content never
leaves the machine except in the final LLM call (which itself is optional and
user-triggered). It also makes the app work with zero API keys.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
from collections import Counter
from datetime import datetime

from models import EmailMessage, ThreadChunk

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    uid INTEGER,
    thread_key TEXT,
    subject TEXT,
    sender TEXT,
    recipients TEXT,
    date_utc TEXT,
    body TEXT,
    folder TEXT,
    PRIMARY KEY (uid, thread_key)
);
CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    thread_key TEXT,
    subject TEXT,
    from_msg_uids TEXT,
    text TEXT,
    n_messages INTEGER,
    last_date TEXT,
    metadata TEXT,
    norm REAL
);
CREATE TABLE IF NOT EXISTS chunk_terms (
    chunk_id TEXT,
    term TEXT,
    weight REAL,
    PRIMARY KEY (chunk_id, term)
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "for", "with",
    "at", "by", "from", "is", "are", "was", "were", "be", "been", "it", "this",
    "that", "as", "i", "you", "he", "she", "we", "they", "my", "your", "his",
    "her", "our", "their", "do", "does", "did", "not", "no", "yes", "so", "if",
    "can", "will", "would", "should", "could", "have", "has", "had", "me", "am",
}

_TOKEN_RE = re.compile(r"[a-z0-9$€£]+")


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in STOPWORDS and len(t) > 1]


def _tfidf_vector(text: str) -> dict[str, float]:
    tf = Counter(tokenize(text))
    if not tf:
        return {}
    max_tf = max(tf.values())
    return {t: (0.5 + 0.5 * c / max_tf) * (1.0 + math.log(c)) for t, c in tf.items()}


def _norm(vec: dict[str, float]) -> float:
    return math.sqrt(sum(w * w for w in vec.values())) or 1.0


class Store:
    def __init__(self, db_path) -> None:
        self.db_path = str(db_path)
        # Streamlit reruns scripts on different threads; allow cross-thread use
        # and serialize access with a lock.
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.conn.executescript(_SCHEMA)

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    # -- messages ------------------------------------------------------------
    def upsert_messages(self, msgs: list[EmailMessage]) -> int:
        with self._lock:
            return self._upsert_messages_impl(msgs)

    def _upsert_messages_impl(self, msgs: list[EmailMessage]) -> int:
        rows = [
            (m.uid, m.thread_key, m.subject, m.sender, m.recipients,
             m.date_utc.isoformat(), m.body, m.folder)
            for m in msgs
        ]
        self.conn.executemany(
            """INSERT INTO messages (uid, thread_key, subject, sender, recipients,
                                     date_utc, body, folder)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(uid, thread_key) DO UPDATE SET
                 subject=excluded.subject, sender=excluded.sender,
                 recipients=excluded.recipients, date_utc=excluded.date_utc,
                 body=excluded.body""",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def count_messages(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

    def all_messages(self) -> list[EmailMessage]:
        out = []
        for r in self.conn.execute("SELECT * FROM messages ORDER BY date_utc DESC"):
            out.append(EmailMessage(
                uid=r["uid"], thread_key=r["thread_key"], subject=r["subject"],
                sender=r["sender"], recipients=r["recipients"],
                date_utc=datetime.fromisoformat(r["date_utc"]),
                body=r["body"], folder=r["folder"],
            ))
        return out

    # -- chunk indexing ------------------------------------------------------
    def index_chunks(self, chunks: list[ThreadChunk]) -> int:
        with self._lock:
            return self._index_chunks_impl(chunks)

    def _index_chunks_impl(self, chunks: list[ThreadChunk]) -> int:
        for c in chunks:
            vec = _tfidf_vector(c.text)
            nrm = _norm(vec)
            self.conn.execute(
                """INSERT INTO chunks (id, thread_key, subject, from_msg_uids, text,
                                       n_messages, last_date, metadata, norm)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     thread_key=excluded.thread_key, subject=excluded.subject,
                     from_msg_uids=excluded.from_msg_uids, text=excluded.text,
                     n_messages=excluded.n_messages, last_date=excluded.last_date,
                     metadata=excluded.metadata, norm=excluded.norm""",
                (c.id, c.thread_key, c.subject, json.dumps(c.from_msg_uids),
                 c.text, c.n_messages,
                 c.last_date.isoformat() if c.last_date else None,
                 json.dumps(c.metadata), nrm),
            )
            self.conn.execute("DELETE FROM chunk_terms WHERE chunk_id=?", (c.id,))
            self.conn.executemany(
                "INSERT INTO chunk_terms (chunk_id, term, weight) VALUES (?,?,?)",
                [(c.id, t, w) for t, w in vec.items()],
            )
        self.conn.commit()
        return len(chunks)

    def chunk_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]

    def all_chunks(self) -> list[ThreadChunk]:
        out = []
        for r in self.conn.execute("SELECT * FROM chunks"):
            out.append(ThreadChunk(
                id=r["id"], thread_key=r["thread_key"], subject=r["subject"],
                from_msg_uids=json.loads(r["from_msg_uids"]),
                text=r["text"], n_messages=r["n_messages"],
                last_date=datetime.fromisoformat(r["last_date"]) if r["last_date"] else None,
                metadata=json.loads(r["metadata"]),
            ))
        return out

    def search(self, query: str, k: int = 6) -> list[tuple[ThreadChunk, float]]:
        """Cosine similarity between the query TF-IDF vector and chunk vectors."""
        qvec = _tfidf_vector(query)
        if not qvec:
            return []
        # IDF from the index: df over chunks containing each query term.
        n_chunks = max(self.chunk_count(), 1)
        idf: dict[str, float] = {}
        for t in qvec:
            df = self.conn.execute(
                "SELECT COUNT(*) FROM chunk_terms WHERE term=?", (t,)
            ).fetchone()[0]
            idf[t] = math.log((n_chunks + 1) / (df + 0.5)) + 1.0

        # Score chunks that share any term with the query.
        scores: dict[str, float] = {}
        norms: dict[str, float] = {}
        for t, qw in qvec.items():
            q_weight = qw * idf[t]
            for r in self.conn.execute(
                "SELECT chunk_id, weight FROM chunk_terms WHERE term=?", (t,)
            ):
                scores[r["chunk_id"]] = scores.get(r["chunk_id"], 0.0) + q_weight * r["weight"]

        out: list[tuple[ThreadChunk, float]] = []
        for cid, raw in scores.items():
            row = self.conn.execute("SELECT * FROM chunks WHERE id=?", (cid,)).fetchone()
            if row is None:
                continue
            norm = row["norm"] or 1.0
            # Normalize by |q| * |d|; |q| computed locally.
            q_norm = _norm({t: qw * idf[t] for t, qw in qvec.items()})
            cos = raw / (norm * q_norm)
            chunk = ThreadChunk(
                id=row["id"], thread_key=row["thread_key"], subject=row["subject"],
                from_msg_uids=json.loads(row["from_msg_uids"]),
                text=row["text"], n_messages=row["n_messages"],
                last_date=datetime.fromisoformat(row["last_date"]) if row["last_date"] else None,
                metadata=json.loads(row["metadata"]),
            )
            # Recency nudge (newer threads rank slightly higher on ties).
            recency = 0.0
            if chunk.last_date:
                age_days = max((datetime.now(chunk.last_date.tzinfo or None) - chunk.last_date).days, 0)
                recency = 0.05 * math.exp(-age_days / 120.0)
            out.append((chunk, cos + recency))

        out.sort(key=lambda x: x[1], reverse=True)
        return out[:k]

    def delete_thread(self, thread_key: str) -> None:
        for cid in [r["id"] for r in self.conn.execute(
                "SELECT id FROM chunks WHERE thread_key=?", (thread_key,))]:
            self.conn.execute("DELETE FROM chunk_terms WHERE chunk_id=?", (cid,))
        self.conn.execute("DELETE FROM chunks WHERE thread_key=?", (thread_key,))
        self.conn.execute("DELETE FROM messages WHERE thread_key=?", (thread_key,))
        self.conn.commit()

    def wipe(self) -> None:
        self.conn.executescript(
            "DELETE FROM messages; DELETE FROM chunks; DELETE FROM chunk_terms;"
        )
        self.conn.commit()
