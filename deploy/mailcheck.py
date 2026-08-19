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

BUT a detector that always fires detects nothing. By 2026-08-19 this mailbox
held 64 messages, 53 of them bounces the e2e journey caused itself: it registers
Keycloak users at synthetic `e2e-<timestamp>@confinia.io` addresses that do not
exist, Keycloak sends their verification mail with alert@ as the envelope
sender, and the 550 comes straight back here. So the check had been failing on
every run since 2026-08-17 -- meaning a real bounce, the thing it exists to
catch, would have been just one more line nobody read.

Hence the split below: bounces for OUR OWN synthetic test recipients are
expected and reported as noise; everything else fails the check. Purging the
noise is deliberate and manual (--purge), never a side effect of looking.
"""
import email
import email.header
import imaplib
import os
import re
import sys

HOST = "ssl0.ovh.net"

# Recipients the e2e journey invents. Mail to them cannot be delivered by
# design, so their bounces say nothing about whether alerting works.
TEST_RECIPIENT = re.compile(r"\b(?:e2e|e2e-harness|reset)-[\w.+-]*@confinia\.io\b", re.I)


def is_bounce(msg) -> bool:
    """Is this a delivery failure report at all, or just mail sent here?

    Worth distinguishing, because they mean opposite things. A bounce says mail
    WE sent did not arrive -- alerting may be broken. Mail merely addressed here
    says someone else's system points at our alert mailbox; a nuisance, not a
    delivery failure. Calling the second one "never delivered" would be a plain
    untruth, and this file exists to be believed.
    """
    if any(part.get_content_type() == "message/delivery-status" for part in msg.walk()):
        return True
    frm = (msg.get("From", "") or "").lower()
    if "mailer-daemon" in frm or "postmaster" in frm:
        return True
    subj = (msg.get("Subject", "") or "").lower()
    return ("undelivered" in subj or "returned to sender" in subj
            or "delivery status notification" in subj or "delivery failure" in subj)


def bounced_recipient(msg) -> str | None:
    """The address a bounce is about -- not the bounce's own From.

    A delivery-status report names the failed recipient in its
    message/delivery-status part; older or non-standard reports only mention it
    in the text. Read both, because being unable to attribute a bounce must
    leave it in the "real" pile, never silently in the noise pile.
    """
    hay = ""
    for part in msg.walk():
        if part.get_content_type() in ("message/delivery-status", "text/plain",
                                       "text/rfc822-headers", "message/rfc822"):
            try:
                hay += (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
            except Exception:
                pass
    m = TEST_RECIPIENT.search(hay)
    return m.group(0) if m else None


def main() -> int:
    purge = "--purge" in sys.argv
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
        typ, data = M.select("INBOX", readonly=not purge)
        if typ != "OK":
            print(f"cannot select INBOX: {typ}", file=sys.stderr)
            return 2
        count = int(data[0])
        if count == 0:
            print(f"{user}: inbox empty, nothing bounced")
            return 0

        typ, ids = M.search(None, "ALL")
        real, noise, foreign = [], [], []
        for num in ids[0].split():
            typ, d = M.fetch(num, "(RFC822)")
            msg = email.message_from_bytes(d[0][1])
            subject = str(email.header.make_header(
                email.header.decode_header(msg.get("Subject", ""))))
            line = f"  {msg.get('Date', '')[:31]} | {subject[:80]}"
            if not is_bounce(msg):
                foreign.append((num, line, None))     # someone else's mail, not a bounce
            elif bounced_recipient(msg):
                noise.append((num, line, None))       # our own synthetic test address
            else:
                real.append((num, line, None))        # the thing this check exists for

        if noise and purge:
            for num, _, _ in noise:
                M.store(num, "+FLAGS", "\\Deleted")
            M.expunge()
            print(f"{user}: purged {len(noise)} self-inflicted e2e bounce(s)")
            noise = []
        if noise:
            print(f"{user}: {len(noise)} bounce(s) for our own e2e test recipients "
                  f"-- expected, and NOT a sign that alerting is broken")
            print(f"  (purge them with: python3 deploy/mailcheck.py --purge)")
        if foreign:
            print(f"{user}: {len(foreign)} message(s) addressed here by another "
                  f"system -- not bounces; someone else's alerts point at us")
            for _, line, _ in foreign[:5]:
                print(line)
        if not real:
            print(f"{user}: nothing of ours bounced")
            return 0 if not foreign else 1

        print(f"{user}: {len(real)} REAL bounce(s) -- mail we sent was never delivered")
        for _, line, _ in real:
            print(line)
        return 1
    finally:
        try:
            M.logout()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
