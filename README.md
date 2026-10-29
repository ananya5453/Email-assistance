# Email Assistant — RAG over your inbox (Streamlit, Python)

Privacy-aware email assistant: connect **Gmail** (IMAP, app password — no Gmail-API setup required), index your last N months of mail, then:

- **Ask** questions like *“when is my flight?”* or *“what did I agree to with client X?”* — answers are grounded in retrieved emails with citations
- **Draft** replies that use the context of the *thread* (not just the matched message)
- **Privacy Lab** — see how the system chunks threads, detects/redacts PII (emails, phones, cards, SSNs, IBANs, OTP codes) with a **local, reversible vault**, and inspect the exact context sent to the LLM
- Retrieval: **local TF-IDF (hashing) + BM25-style scoring, zero network**, so your email content never leaves your machine except the final LLM call (optional)

## Quick start

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Connecting Gmail

1. Enable 2-Step Verification on your Google account
2. Create an **App Password**: Google Account → Security → App passwords
3. In the app: sidebar → **Connect Gmail** → email + app password → Connect
4. Choose months of history → **Sync now**

Everything is stored locally in `~/.email_assistant/` (SQLite + vector store + PII vault). The IMAP password is kept in memory for the session only.

## Demo mode

No Gmail handy? Toggle **Demo mode** in the sidebar to load a synthetic inbox (flights, client negotiations, OTP emails) and exercise every feature offline.

## Architecture

```
gmail_client.py   IMAP fetch (last N months), thread detection via subject + References
chunker.py        Thread-aware chunking (groups consecutive messages of one thread)
pii.py            Regex + context PII detection, redaction, local reversible vault
store.py          SQLite messages + chunks, vector search via Chroma (TF-IDF fallback)
rag.py            Prompt building, grounded Q&A, reply drafting
llm.py            LLM providers (OpenAI-compatible / Anthropic / Ollama)
demo_data.py      Synthetic inbox for offline use
app.py            Streamlit UI
```

## Tests

```bash
pytest -q
```

Covers chunking, PII precision/recall, retrieval quality, and vault round-trip.
