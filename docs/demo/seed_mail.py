"""A mailbox with invented mail for the pictures — needs the `mail` service of the demo stack.

GreenMail creates an account the moment somebody logs in, so the messages are simply
delivered over SMTP. Everything in here is made up: the senders end in example.org, the
amounts are round, and nobody is real.

    python3 docs/demo/seed_mail.py
"""
from __future__ import annotations

import datetime as dt
import email.utils
import smtplib
import sys
from email.message import EmailMessage

HOST, PORT = "localhost", 3025          # GreenMail SMTP, exposed by the demo compose
TO = "ada@example.org"

MAILS = [
    ("Invoice 2026-0815", "billing@utility.example.org", "Example Utility",
     "Dear customer,\n\nplease find your invoice for August 2026 over 84.20 EUR.\n"
     "The amount will be debited from your account on 2026-09-01.\n\n"
     "Kind regards\nExample Utility", 5),
    ("Your order is on its way", "shipping@shop.example.org", "Example Shop",
     "Hello Ada,\n\nyour order #4711 has left our warehouse and arrives tomorrow.\n\n"
     "Tracking number: 00340000000000000000", 20),
    ("Appointment confirmation, workshop", "appointments@workshop.example.org", "Example Workshop",
     "Dear Ms Lovelace,\n\nwe confirm your appointment on 2026-09-04 at 09:00.\n"
     "Please bring the vehicle registration with you.", 90),
    ("Minutes of the board meeting", "board@club.example.org", "Example Club",
     "Dear Ada,\n\nplease find the minutes of the meeting of 2026-08-18.\n"
     "Item 4 (buying a measuring device) still needs your answer.", 240),
    ("Newsletter: news from the workshop", "news@shop.example.org", "Example Shop",
     "This month: tools on offer, new opening hours and a workshop report.\n\n"
     "Unsubscribe: https://shop.example.org/unsubscribe", 400),
    ("Your account has been suspended — act now", "security@bank-verify.example.org", "Bank Security",
     "Dear customer,\n\nwe noticed unusual activity and have temporarily suspended your "
     "account. Confirm your details within 24 hours at\n"
     "http://bank-verify.example.org/login\n\nYour security team", 30),
]


def main() -> None:
    now = dt.datetime.now(dt.timezone.utc)
    with smtplib.SMTP(HOST, PORT, timeout=10) as smtp:
        for subject, addr, name, body, minutes_ago in MAILS:
            m = EmailMessage()
            m["Subject"] = subject
            m["From"] = email.utils.formataddr((name, addr))
            m["To"] = TO
            m["Date"] = email.utils.format_datetime(now - dt.timedelta(minutes=minutes_ago))
            m["Message-ID"] = email.utils.make_msgid(domain="example.org")
            m.set_content(body)
            smtp.send_message(m)
    print(f"{len(MAILS)} mails delivered to {TO}")


if __name__ == "__main__":
    main()
