"""Anything in alert@confinia.io's INBOX is a finding (RULES 17).

That mailbox is SEND-ONLY by design -- it emits Grafana alerts and Keycloak
transactional mail to ALERT_TO, and its quota is deliberately tiny. So it should
be empty. What lands there instead is bounces: the mail system telling us that
something we sent was never delivered.

Which makes it the one place where "the alerting itself is broken" is visible,
and it had never been read. On 2026-08-11 it held a bounce nobody saw --
`550 5.1.1 <contact@confinia.io>: Recipient address rejected: User unknown` --
meaning every alert sent that day went nowhere. The mailbox was created later
and delivery works now, but the failure was silent for as long as it lasted:
Grafana reports a send as successful once the SMTP server accepts it, and a
bounce arrives afterwards, out of band.

Reads with the SMTP account's own credentials -- OVH serves IMAP on the same
login, which is why no extra secret is needed.
"""
import email
import email.header
import imaplib
import os
import sys

HOST = "ssl0.ovh.net"


def main() -> int:
    user = os.environ.get("GF_SMTP_USER")
    pw = os.environ.get("GF_SMTP_PASSWORD")
    if not (user and pw):
        print("no GF_SMTP_USER/GF_SMTP_PASSWORD in the environment", file=sys.stderr)
        return 2
    try:
        M = imaplib.IMAP4_SSL(HOST, 993, timeout=20)
        M.login(user, pw)
    except Exception as e:
        # Not being able to look is not the same as nothing being there.
        print(f"CANNOT READ {user}: {type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
        return 2

    try:
        typ, data = M.select("INBOX", readonly=True)
        if typ != "OK":
            print(f"cannot select INBOX: {typ}", file=sys.stderr)
            return 2
        count = int(data[0])
        if count == 0:
            print(f"{user}: inbox empty, nothing bounced")
            return 0

        print(f"{user}: {count} message(s) -- this mailbox should be EMPTY")
        typ, ids = M.search(None, "ALL")
        for num in ids[0].split():
            typ, d = M.fetch(num, "(BODY.PEEK[HEADER.FIELDS (DATE FROM SUBJECT)])")
            msg = email.message_from_bytes(d[0][1])
            subject = str(email.header.make_header(
                email.header.decode_header(msg.get("Subject", ""))))
            print(f"  {msg.get('Date', '')[:31]} | {subject[:80]}")
        return 1
    finally:
        try:
            M.logout()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
