"""RAG orchestration: retrieve → redact → prompt → generate, plus reply drafting
that re-identifies PII locally before handing a draft back to the user.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from chunker import chunk_threads
from llm import DEFAULT_SYSTEM_PROMPT, LLM, build_user_prompt
from models import EmailMessage
from pii import PiiVault
from store import Store


@dataclass
class Citation:
    n: int
    chunk_id: str
    subject: str
    last_date: str
    score: float
    redacted_text: str
    text: str  # re-identified, for local display


@dataclass
class RagResult:
    answer: str
    citations: list[Citation] = field(default_factory=list)
    context_chars: int = 0
    pii_redacted: int = 0


def _fmt_last_date(dt) -> str:
    return dt.strftime("%Y-%m-%d") if dt else "?"


class Assistant:
    def __init__(self, store: Store, vault: PiiVault) -> None:
        self.store = store
        self.vault = vault

    def retrieve(self, query: str, k: int = 6):
        return self.store.search(query, k=k)

    def _build_context(
        self, query: str, k: int
    ) -> tuple[list[Citation], int, int]:
        hits = self.store.search(query, k=k)
        citations: list[Citation] = []
        redacted_total = 0
        context_chars = 0
        for n, (chunk, score) in enumerate(hits, start=1):
            red = self.vault.redact(chunk.text)
            redacted_total += len(red.findings)
            context_chars += len(red.text)
            citations.append(Citation(
                n=n,
                chunk_id=chunk.id,
                subject=chunk.subject,
                last_date=_fmt_last_date(chunk.last_date),
                score=round(score, 4),
                redacted_text=red.text,
                text=chunk.text,
            ))
        return citations, context_chars, redacted_total

    def ask(self, question: str, llm: LLM | None, k: int = 6) -> RagResult:
        citations, ctx_chars, redacted = self._build_context(question, k)
        if not citations:
            return RagResult(
                answer="I could not find any relevant emails in the index for that question.",
                citations=[], context_chars=0, pii_redacted=0,
            )

        if llm is None:
            # No-LLM mode: return the top excerpts verbatim (locally computed).
            lines = ["No LLM configured — here are the most relevant excerpts:"]
            for c in citations[:3]:
                snippet = c.text[:500].replace("\n", "\n> ")
                lines.append(f"\n[{c.n}] ({c.subject}, {c.last_date})\n> {snippet}")
            return RagResult("\n".join(lines), citations, ctx_chars, redacted)

        excerpts = [(f"[{c.n}]", c.redacted_text) for c in citations]
        prompt = build_user_prompt(question, excerpts)
        try:
            answer = llm.chat(DEFAULT_SYSTEM_PROMPT, prompt)
        except Exception as e:
            answer = f"LLM call failed: {e}\n\nTop excerpts:\n"
            for c in citations[:3]:
                answer += f"\n[{c.n}] ({c.subject}, {c.last_date})\n> {c.text[:400]}"
        return RagResult(answer, citations, ctx_chars, redacted)

    def draft_reply(self, thread_key: str, llm: LLM, extra_instructions: str = "",
                    k: int = 4) -> tuple[str, list[Citation], int]:
        """Draft a reply to a thread using the whole thread as context.

        Returns (draft, citations, chars_sent).
        """
        # Retrieve chunks from this thread only.
        hits = self.store.search(thread_key, k=k * 4)
        thread_chunks = [c for c, s in hits if c.thread_key == thread_key]
        if not thread_chunks:
            thread_chunks = [c for c in self.store.all_chunks()
                             if c.thread_key == thread_key]
        if not thread_chunks:
            return "No indexed messages for this thread.", [], 0

        thread_chunks.sort(key=lambda c: c.last_date or 0)
        redacted_parts = []
        citations: list[Citation] = []
        chars = 0
        for n, chunk in enumerate(thread_chunks, start=1):
            red = self.vault.redact(chunk.text)
            chars += len(red.text)
            redacted_parts.append(f"--- [{n}] ---\n{red.text}")
            citations.append(Citation(
                n=n,
                chunk_id=chunk.id,
                subject=chunk.subject,
                last_date=_fmt_last_date(chunk.last_date),
                score=0.0,
                redacted_text=red.text,
                text=chunk.text,
            ))

        system = (
            "You are drafting a reply email on behalf of the user. Use the thread "
            "context for facts and tone. Output ONLY the reply body — no subject "
            "line, no preamble. Keep it short, concrete, and human. Cite nothing."
        )
        user = (
            "Thread so far:\n\n" + "\n\n".join(redacted_parts)
            + f"\n\nWrite the user's next reply."
            + (f" Extra instructions: {extra_instructions}" if extra_instructions else "")
        )
        draft_redacted = llm.chat(system, user, max_tokens=500)
        # Re-identify so the draft is usable (local-only operation).
        draft, _ = self.vault.reidentify(draft_redacted)
        return draft, citations, chars
