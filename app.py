"""Email Assistant — Streamlit UI.

Run: streamlit run app.py
"""
from __future__ import annotations

import html
from datetime import datetime, timezone 

import streamlit as st 

from chunker import chunk_threads
from config import APP_DIR
from demo_data import demo_messages 
from gmail_client import GmailClient 
from pii import PiiVault 
from rag import Assistant 
from store import Store

st.set_page_config(
    page_title="Email Assistant",
    page_icon="📬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --- session state ------------------------------------------------------------
if "store" not in st.session_state:
    st.session_state.store = Store(APP_DIR / "assistant.db")
if "vault" not in st.session_state:
    st.session_state.vault = PiiVault()
if "assistant" not in st.session_state:
    st.session_state.assistant = Assistant(st.session_state.store, st.session_state.vault)
if "demo_loaded" not in st.session_state:
    st.session_state.demo_loaded = False
if "history" not in st.session_state:
    st.session_state.history = []


def refresh_assistant() -> None:
    st.session_state.assistant = Assistant(st.session_state.store, st.session_state.vault)


# --- sidebar -------------------------------------------------------------------
with st.sidebar:
    st.title("Email Assistant")
    st.caption(f"Data dir: `{APP_DIR}`")

    mode = st.radio("Mode", ["Demo mode", "Connect Gmail"], horizontal=True)

    creds = {}
    client = None
    if mode == "Connect Gmail":
        with st.expander("Gmail connection (IMAP + App Password)", expanded=False):
            st.markdown(
                "1. Enable 2-Step Verification\n"
                "2. Create an **App Password** (Google Account → Security)\n"
                "3. Enter it below — it is never written to disk"
            )
            email_addr = st.text_input("Gmail address", placeholder="you@gmail.com")
            app_pw = st.text_input("App password", type="password")
            if st.button("Test connection"):
                if email_addr and app_pw:
                    with st.spinner("Connecting…"):
                        ok = GmailClient(email_addr, app_pw).test_connection()
                    st.success("Connected ✔" if ok else "Login failed — check the app password")
                else:
                    st.warning("Enter both fields")

        months = st.slider("Months of history to index", 1, 12, 3)
        if st.button(" Sync now", type="primary", disabled=not (email_addr and app_pw)):
            with st.spinner("Fetching from Gmail…"):
                gc = GmailClient(email_addr, app_pw)
                try:
                    msgs = gc.fetch_recent(months=months)
                    st.session_state.store.upsert_messages(msgs)
                    chunks = chunk_threads(msgs)
                    st.session_state.store.index_chunks(chunks)
                    st.success(f"Indexed {len(msgs)} messages → {len(chunks)} chunks")
                finally:
                    gc.close()

    else:  # Demo mode
        if st.button("Load demo inbox", type="primary"):
            msgs = demo_messages()
            st.session_state.store.upsert_messages(msgs)
            st.session_state.store.index_chunks(chunk_threads(msgs))
            st.session_state.demo_loaded = True
            st.success(f"Loaded {len(msgs)} demo messages")

    if st.button("🧹 Wipe local index & vault"):
        st.session_state.store.wipe()
        st.session_state.vault = PiiVault.__new__(PiiVault)
        st.session_state.vault.path = APP_DIR / "vault.json"
        st.session_state.vault._map = {}
        st.session_state.vault._reverse = {}
        st.session_state.vault._counters = {}
        refresh_assistant()
        st.success("Local data cleared")

    st.divider()
    n_msgs = st.session_state.store.count_messages()
    n_chunks = st.session_state.store.chunk_count()
    st.metric("Messages indexed", n_msgs)
    st.metric("Chunks", n_chunks)
    pii_stats = st.session_state.vault.stats()
    if pii_stats:
        st.write("**PII vault:** " + ", ".join(f"{k}: {v}" for k, v in pii_stats.items()))


# --- LLM settings ----------------------------------------------------------------
with st.sidebar.expander("🤖 LLM settings (optional)"):
    st.caption("Without an LLM, Ask returns the top raw excerpts.")
    provider = st.selectbox("Provider", ["None", "OpenAI-compatible", "Anthropic", "Ollama (local)"])
    model = st.text_input("Model", value="gpt-4o-mini" if provider == "OpenAI-compatible"
                          else "claude-3-5-sonnet-latest" if provider == "Anthropic"
                          else "llama3.1")
    api_key = st.text_input("API key", type="password")
    base_url = st.text_input("Base URL (optional)",
                             placeholder="https://api.openai.com/v1")


def make_llm():
    if provider == "None":
        return None
    from llm import LLM
    return LLM(
        provider={"OpenAI-compatible": "openai", "Anthropic": "anthropic",
                  "Ollama (local)": "ollama"}[provider],
        model=model or "gpt-4o-mini",
        api_key=api_key,
        base_url=base_url,
    )


# --- main tabs ---------------------------------------------------------------------
tab_ask, tab_draft, tab_privacy, tab_inbox = st.tabs(
    ["🔎 Ask", "✍️ Draft reply", "🛡️ Privacy Lab", "🗂️ Inbox"]
)

# --- Ask tab -------------------------------------------------------------------------
with tab_ask:
    st.subheader("Ask your inbox")
    with st.form("ask_form", clear_on_submit=False):
        q = st.text_input(
            "Question",
            placeholder="e.g. When is my flight? What did I agree to with Aurora Labs?",
        )
        c1, c2 = st.columns([1, 6])
        with c1:
            go = st.form_submit_button("Ask", type="primary")
        with c2:
            k = st.slider("Context size (chunks)", 2, 10, 6, key="k_ask")

    if go:
        if not q:
            st.warning("Type a question first.")
        else:
            with st.spinner("Retrieving…"):
                res = st.session_state.assistant.ask(q, llm=make_llm(), k=k)
            st.markdown(res.answer)
            st.caption(
                f"Context sent: {res.context_chars:,} chars · PII redacted: {res.pii_redacted}"
            )
            if res.citations:
                with st.expander("📚 Sources (re-identified locally for display)"):
                    for cit in res.citations:
                        st.markdown(
                            f"**[{cit.n}] {cit.subject}** · last activity {cit.last_date}"
                            f" · score `{cit.score}` · chunk `{cit.chunk_id}`"
                        )
                        st.text(cit.text)

# --- Draft tab --------------------------------------------------------------------------
with tab_draft:
    st.subheader("Draft a reply with thread context")
    msgs = st.session_state.store.all_messages()
    threads = sorted({m.thread_key for m in msgs})
    if not threads:
        st.info("Index some email first (sidebar → Sync now / Load demo inbox).")
    else:
        thread = st.selectbox("Thread", threads)
        instr = st.text_area("Extra instructions (optional)",
                             placeholder="e.g. accept the terms, ask about QA ownership")
        if st.button("✍️ Draft", type="primary"):
            llm = make_llm()
            if llm is None:
                st.warning("Drafting needs an LLM — configure one in the sidebar.")
            else:
                with st.spinner("Drafting…"):
                    draft, cites, chars = st.session_state.assistant.draft_reply(
                        thread, llm, extra_instructions=instr)
                st.text_area("Draft (re-identified locally)", value=draft, height=220)
                st.caption(f"Thread context sent: {chars:,} chars (PII redacted)")
                with st.expander("What the model saw (redacted)"):
                    for cit in cites:
                        st.markdown(f"**[{cit.n}] {cit.subject}** · {cit.last_date}")
                        st.text(cit.redacted_text)

# --- Privacy Lab --------------------------------------------------------------------------
with tab_privacy:
    st.subheader("Privacy Lab")
    st.markdown(
        "See exactly how your mail is **chunked**, what **PII** is detected, and the "
        "**redacted context** that would be sent to an LLM. Redaction is reversible "
        "via a local vault (`~/.email_assistant/vault.json`, chmod 600) — nothing is "
        "sent anywhere unless you press Ask/Draft with an LLM configured."
    )
    all_msgs = st.session_state.store.all_messages()
    if not all_msgs:
        st.info("Load the demo inbox or sync Gmail to explore.")
    else:
        thread = st.selectbox("Thread", sorted({m.thread_key for m in all_msgs}),
                              key="privacy_thread")
        tmsgs = [m for m in all_msgs if m.thread_key == thread]
        tmsgs.sort(key=lambda m: m.date_utc)
        chunks = chunk_threads(tmsgs)

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**1️⃣ Raw messages**")
            for m in tmsgs:
                st.markdown(f"`{m.date_utc:%Y-%m-%d}` **{m.sender}**")
                st.text(m.body[:600])
        with col2:
            st.markdown("**2️⃣ Thread chunks (retrieval units)**")
            for c in chunks:
                red = st.session_state.vault.redact(c.text)
                st.markdown(
                    f"`{c.id}` — {c.n_messages} msgs · {len(c.text):,} chars · "
                    f"{len(red.findings)} PII items"
                )
                st.text(red.text[:1200])

        st.markdown("**3️⃣ PII found across this thread**")
        vault = st.session_state.vault
        seen = {}
        for m in tmsgs:
            for f in vault.detect(m.body):
                seen[f.value] = f.kind
        if seen:
            st.dataframe(
                [{"kind": k, "original": v} for v, k in seen.items()],
                use_container_width=True,
                hide_index=True,
            )
            st.caption("These originals are stored only in your local vault file.")
        else:
            st.success("No PII detected in this thread.")

# --- Inbox --------------------------------------------------------------------------------
with tab_inbox:
    st.subheader("Indexed inbox")
    msgs = st.session_state.store.all_messages()
    if not msgs:
        st.info("Nothing indexed yet.")
    else:
        st.dataframe(
            [{
                "date": m.date_utc.strftime("%Y-%m-%d"),
                "from": m.sender,
                "subject": m.subject,
                "thread": m.thread_key,
            } for m in msgs],
            use_container_width=True,
            hide_index=True,
        )
        st.caption(f"{len(msgs)} messages · stored locally in SQLite at {APP_DIR}")
