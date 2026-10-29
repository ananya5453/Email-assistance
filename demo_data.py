"""Synthetic inbox for demo mode — no network needed."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from models import EmailMessage


def _msg(uid, thread_key, subject, sender, recipients, days_ago, body):
    return EmailMessage(
        uid=uid,
        thread_key=thread_key,
        subject=subject,
        sender=sender,
        recipients=recipients,
        date_utc=datetime.now(timezone.utc) - timedelta(days=days_ago),
        body=body,
    )


def demo_messages() -> list[EmailMessage]:
    return [
        _msg(1, "boarding pass qf12 sydney to sfo", "Boarding pass QF12 Sydney → SFO",
             "itinerary@qantas.com", "you@example.com", 42,
             """QANTAS BOOKING QF-8Z4K1
Passenger: JORDAN SMITH (Jordan Smith)
Flight QF12  Sydney (SYD) → San Francisco (SFO)
Departs: Fri 14 Aug 2026 09:55 AEST — Terminal 1
Arrives: 14 Aug 2026 06:40 PDT — International Terminal A
Seat 32A, Economy
Booking reference: QF8Z4K1
Check-in opens 48h before departure."""),

        _msg(2, "boarding pass qf12 sydney to sfo", "Re: Boarding pass QF12 Sydney → SFO",
             "no-reply@qantas.com", "you@example.com", 40,
             """Your check-in for QF12 is now open. Booking QF8Z4K1.
Your OTP for online check-in is 448210. Do not share this code.
Gate 9, boarding 09:10."""),

        _msg(3, "aurora labs kickoff", "Aurora Labs — kickoff notes",
             "maya.chen@auroralabs.io", "you@example.com", 30,
             """Hi,
Recap of yesterday:
- Scope: dashboard v2, 6 screens, brand refresh included
- Timeline: kickoff to first internal demo in 4 weeks
- Rate: $145/h, invoiced monthly, net-30
- We'll send the contract via DocuSign this week.
Maya Chen
Aurora Labs, 408-555-0199"""),

        _msg(4, "aurora labs kickoff", "Re: Aurora Labs — kickoff notes",
             "you@example.com", "maya.chen@auroralabs.io", 29,
             """Thanks Maya — confirmed on my side:
- 6 screens, brand refresh included
- $145/h net-30, invoiced monthly
- First internal demo in 4 weeks
One more thing: can you confirm who owns QA? I'd like to avoid last project's handoff issues."""),

        _msg(5, "aurora labs kickoff", "Re: Aurora Labs — kickoff notes",
             "maya.chen@auroralabs.io", "you@example.com", 27,
             """QA is owned by our side (Priya's team); you hand off test cases at demo-1.
Contract sent via DocuSign — it reflects $145/h net-30 and the 6-screen scope.
PO number for invoicing: AUR-2026-114."""),

        _msg(6, "client contract northwind", "Contract — Northwind retainer",
             "legal@northwind.example", "you@example.com", 21,
             """Attached is the master services agreement for the Northwind retainer.
Key terms: 20 hours/week, $130/h, 3-month initial term, 30-day notice.
Liability capped at fees paid in prior 60 days. Governing law: Delaware.
Please sign via e-signature by Friday. Contact: Dana Reyes, (415) 555-0134."""),

        _msg(7, "client contract northwind", "Re: Contract — Northwind retainer",
             "you@example.com", "legal@northwind.example", 20,
             """Two edits before I sign:
1) Section 4: change 30-day notice to 45 days.
2) Add clause that pre-existing IP remains mine.
Everything else works — 20h/week at $130/h is fine."""),

        _msg(8, "client contract northwind", "Re: Contract — Northwind retainer",
             "legal@northwind.example", "you@example.com", 18,
             """Both edits accepted. Section 4 now reads 45 days, and Section 7.2 carves out
your pre-existing IP. Countersigned copy attached. Welcome aboard!
Dana Reyes
Northwind Legal, 415-555-0134"""),

        _msg(9, "your verification code", "Your verification code",
             "security@bankone.example", "you@example.com", 5,
             """Your one-time code is 771903. It expires in 10 minutes.
Never share this code. If you didn't request it, call the number on your card."""),

        _msg(10, "flight change qf12", "Schedule change: QF12 Sydney → SFO",
             "itinerary@qantas.com", "you@example.com", 3,
             """Important: your flight QF12 on 14 Aug 2026 now departs at 11:20 AEST (was 09:55).
Arrival SFO approx 08:05 PDT. Booking QF8Z4K1. Seat unchanged (32A).
No action required if the new time works; otherwise rebook online."""),

        _msg(11, "sprint planning notes", "Sprint 14 planning notes",
             "pm@clientco.example", "you@example.com", 10,
             """Sprint 14 (Aug 24 – Sep 4):
- You: payments refactor + code review rotation
- Dependency: API team ships /v2/invoices by Aug 27
- Demo every second Thursday, 10am Pacific."""),

        _msg(12, "sprint planning notes", "Re: Sprint 14 planning notes",
             "you@example.com", "pm@clientco.example", 9,
             """Works for me. I'll take the payments refactor first so the API dependency
lands mid-sprint. For reviews, I can cover Tue/Thu."""),
    ]
