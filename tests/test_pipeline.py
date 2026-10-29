import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from chunker import chunk_threads
from models import EmailMessage, ThreadChunk
from pii import PiiVault, _luhn_ok
from store import Store


def _msg(uid, thread_key, subject, sender, days_ago, body):
    return EmailMessage(
        uid=uid, thread_key=thread_key, subject=subject, sender=sender,
        recipients="you@example.com",
        date_utc=datetime.now(timezone.utc) - timedelta(days=days_ago),
        body=body,
    )


# --- chunker -----------------------------------------------------------------
def test_thread_grouping_and_quoting():
    msgs = [
        _msg(1, "t1", "Re: Project", "a@x.com", 3, "Latest point.\n> old quoted\nOn Mon, someone wrote:\n> more old"),
        _msg(2, "t1", "Project", "b@y.com", 5, "Earlier point."),
    ]
    chunks = chunk_threads(msgs)
    assert len(chunks) == 1
    c = chunks[0]
    assert c.n_messages == 2
    assert "old quoted" not in c.text
    assert "Latest point." in c.text
    assert "Earlier point." in c.text
    # header line present for standalone context
    assert c.text.startswith("Email thread:")


def test_long_threads_split_at_message_boundaries():
    msgs = [
        _msg(1, "t1", "S", f"s{i}@x.com", 1, "x" * 900)
        for i in range(5)
    ]
    # use distinct senders so messages are distinguishable
    msgs = [
        _msg(i, "t1", "S", f"s{i}@x.com", 1, "x" * 900)
        for i in range(5)
    ]
    chunks = chunk_threads(msgs)
    assert len(chunks) >= 2
    for c in chunks:
        assert len(c.text) <= 2200 + 300  # MAX_CHARS + header allowance


# --- PII ----------------------------------------------------------------------
def test_pii_detection_and_redaction_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        vault = PiiVault(path=os.path.join(td, "vault.json"))
        text = "Email maya@auroralabs.io or call 408-555-0199. Card 4111 1111 1111 1111."
        res = vault.redact(text)
        assert "maya@auroralabs.io" not in res.text
        assert "maya@auroralabs.io" in text  # original untouched
        assert "[EMAIL_" in res.text
        assert "[PHONE_" in res.text
        assert "[CARD_" in res.text
        # reversible
        back, n = vault.reidentify(res.text)
        assert back == text
        assert n == 3


def test_pii_stable_surrogates():
    with tempfile.TemporaryDirectory() as td:
        vault = PiiVault(path=os.path.join(td, "vault.json"))
        a = vault.redact("From: zoe@x.com about the contract")
        b = vault.redact("zoe@x.com again")
        assert "[EMAIL_1]" in a.text and "[EMAIL_1]" in b.text


def test_luhn():
    assert _luhn_ok("4111111111111111")
    assert not _luhn_ok("4111111111111112")


def test_otp_detection():
    with tempfile.TemporaryDirectory() as td:
        vault = PiiVault(path=os.path.join(td, "vault.json"))
        res = vault.redact("Your OTP is 771903.")
        assert "771903" not in res.text
        assert "[OTP_" in res.text


# --- store / retrieval ----------------------------------------------------------
def test_store_roundtrip_and_search():
    with tempfile.TemporaryDirectory() as td:
        store = Store(os.path.join(td, "t.db"))
        msgs = [
            _msg(1, "flight", "Boarding pass QF12", "airline@x.com", 40,
                 "Flight QF12 departs Sydney 09:55, seat 32A, booking QF8Z4K1"),
            _msg(2, "flight", "Re: Boarding pass QF12", "airline@x.com", 3,
                 "Schedule change: QF12 now departs 11:20"),
            _msg(3, "contract", "Contract terms", "legal@y.com", 20,
                 "Rate is $145/h net-30, 45 day notice, Delaware law"),
        ]
        store.upsert_messages(msgs)
        assert store.count_messages() == 3
        chunks = chunk_threads(msgs)
        store.index_chunks(chunks)
        assert store.chunk_count() == 2

        hits = store.search("when does my flight depart", k=2)
        assert hits and hits[0][0].thread_key == "flight"
        assert hits[0][1] > 0.0

        hits2 = store.search("contract rate notice", k=2)
        assert hits2 and hits2[0][0].thread_key == "contract"
        store.close()


def test_no_llm_answer_mode():
    from rag import Assistant
    with tempfile.TemporaryDirectory() as td:
        store = Store(os.path.join(td, "t.db"))
        try:
            vault = PiiVault(path=os.path.join(td, "vault.json"))
            msgs = [
                _msg(1, "flight", "Boarding pass QF12", "airline@x.com", 40,
                     "Flight QF12 departs Sydney 09:55, seat 32A"),
            ]
            store.upsert_messages(msgs)
            store.index_chunks(chunk_threads(msgs))
            a = Assistant(store, vault)
            res = a.ask("when is my flight", llm=None)
            assert "excerpts" in res.answer.lower()
            assert res.citations and res.citations[0].n == 1
        finally:
            store.close()
