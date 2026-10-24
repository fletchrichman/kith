"""Commands that read message and email text live from Messages and Mail (never stored)."""
import sys
from datetime import datetime, timezone

from kith.commands.people import group_label
from kith.config import MESSAGES_DB
from kith.db import one_person
from kith.sources.base import open_source
from kith.sources.mail import email_body, find_emlx, mail_index
from kith.sources.messages import decode_attributed_body
from kith.util import apple_ts, norm_email, norm_handle


def cmd_grep(db, a):
    """Find conversations mentioning something, across everyone (read live from Messages)."""
    src = open_source(MESSAGES_DB, "Messages")
    q = a.text
    variants = {q, q.lower(), q.capitalize(), q.title()}
    ab = " OR ".join("instr(m.attributedBody, CAST(? AS BLOB)) > 0" for _ in variants)
    rows = src.execute(f"""SELECT m.date, m.is_from_me, m.text, m.attributedBody, m.handle_id, c.ROWID AS chat_id,
            c.style, c.display_name FROM message m JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
            JOIN chat c ON c.ROWID = cmj.chat_id
            WHERE m.associated_message_type = 0 AND coalesce(m.item_type, 0) = 0 AND (m.text LIKE ? OR {ab})
            ORDER BY m.date DESC LIMIT ?""", (f"%{q}%", *variants, a.limit * 3)).fetchall()
    handles = {r[0]: norm_handle(r[1]) for r in src.execute("SELECT ROWID, id FROM handle")}
    chat_handle = {r[0]: handles.get(r[1]) for r in src.execute("SELECT chat_id, handle_id FROM chat_handle_join")}
    src.close()
    def name_of(h):
        r = db.execute("SELECT p.name FROM identities i JOIN people p ON p.id = i.person_id WHERE i.value=?", (h,)).fetchone()
        return r[0] if r else h
    shown = 0
    for r in rows:
        text = r["text"] or decode_attributed_body(r["attributedBody"]) or ""
        i = text.lower().find(q.lower())
        if i < 0:
            continue
        snippet = text[max(0, i - 70): i + len(q) + 70].replace("\n", " ")
        if r["style"] == 43:
            where = r["display_name"] or group_label(db, r["chat_id"])
            who = "me" if r["is_from_me"] else name_of(handles.get(r["handle_id"]))
            label = f"[{where}] {who}"
        else:
            other = name_of(chat_handle.get(r["chat_id"]))
            label = f"me -> {other}" if r["is_from_me"] else f"{other}"
        print(f"{apple_ts(r['date'])[:10]} {label}: ...{snippet.strip()}...")
        shown += 1
        if shown >= a.limit:
            break
    if not shown:
        print(f"No messages mention '{q}'.")


def cmd_messages(db, a):
    """Read message text live from Messages (never stored in the CRM)."""
    p = one_person(db, a.query)
    handles = [r[0] for r in db.execute("SELECT value FROM identities WHERE person_id=? AND kind IN ('phone','email')", (p["id"],))]
    if not handles:
        sys.exit("No phone/email for this person.")
    src = open_source(MESSAGES_DB, "Messages")
    ids = [r["ROWID"] for r in src.execute("SELECT ROWID, id FROM handle") if norm_handle(r["id"]) in handles]
    if not ids:
        sys.exit("No Messages history for this person's numbers/emails.")
    qs = ",".join("?" * len(ids))
    where = "c.style = 45" if not a.groups else "1=1"
    rows = src.execute(f"""
        SELECT m.date, m.is_from_me, m.text, m.attributedBody, c.style, c.display_name
        FROM message m JOIN chat_message_join cmj ON cmj.message_id = m.ROWID JOIN chat c ON c.ROWID = cmj.chat_id
        WHERE {where} AND m.associated_message_type = 0 AND coalesce(m.item_type, 0) = 0 AND (
          (c.style = 45 AND c.ROWID IN (SELECT chat_id FROM chat_handle_join WHERE handle_id IN ({qs})))
          OR (c.style = 43 AND m.handle_id IN ({qs})))
        ORDER BY m.date DESC LIMIT ?""", (*ids, *ids, a.limit * 4 if a.grep else a.limit)).fetchall()
    out = []
    for r in rows:
        text = r["text"] or decode_attributed_body(r["attributedBody"]) or "[attachment/no text]"
        if a.grep and a.grep.lower() not in text.lower():
            continue
        who = "me" if r["is_from_me"] else p["name"].split()[0]
        where_ = f" [{r['display_name'] or 'group'}]" if r["style"] == 43 else ""
        out.append(f"{apple_ts(r['date'])[:16]}{where_} {who}: {text.strip()}")
    for line in reversed(out[: a.limit]):
        print(line)


def cmd_emails(db, a):
    """Recent email with a person: subjects (and optionally opening lines), read live from Mail."""
    p = one_person(db, a.query)
    mine = {r[0] for r in db.execute("SELECT value FROM identities WHERE person_id=? AND kind='email'", (p["id"],))}
    if not mine:
        sys.exit("No email address for this person.")
    me = {r[0] for r in db.execute("SELECT address FROM self_addresses")}
    src = open_source(mail_index(), "Mail")
    addr = {r[0]: norm_email(r[1]) for r in src.execute("SELECT ROWID, address FROM addresses")}
    theirs = [i for i, e in addr.items() if e in mine]
    qs = ",".join("?" * len(theirs))
    skip = """(SELECT ROWID FROM mailboxes WHERE lower(url) LIKE '%spam%' OR lower(url) LIKE '%junk%'
               OR lower(url) LIKE '%trash%' OR lower(url) LIKE '%deleted%' OR lower(url) LIKE '%draft%')"""
    rows = src.execute(f"""
        SELECT m.ROWID, m.date_received, m.sender, coalesce(m.subject_prefix, '') || coalesce(s.subject, '') AS subject,
               sm.summary, coalesce(m.global_message_id, -m.ROWID) AS gid,
               (SELECT count(*) FROM recipients r WHERE r.message = m.ROWID) AS n_rcpt
        FROM messages m LEFT JOIN subjects s ON s.ROWID = m.subject LEFT JOIN summaries sm ON sm.ROWID = m.summary
        WHERE m.mailbox NOT IN {skip} AND coalesce(m.deleted, 0) = 0
          AND (m.sender IN ({qs}) OR m.ROWID IN (SELECT message FROM recipients WHERE address IN ({qs})))
        ORDER BY m.date_received DESC LIMIT ?""", (*theirs, *theirs, a.limit * 5)).fetchall()
    src.close()
    seen, out = set(), []
    for r in rows:
        if r["gid"] in seen:  # Gmail copies (Inbox + All Mail) count once
            continue
        seen.add(r["gid"])
        if a.grep and a.grep.lower() not in (r["subject"] + " " + (r["summary"] or "")).lower():
            continue
        out.append(r)
        if len(out) >= a.limit:
            break
    bodies = find_emlx([r["ROWID"] for r in out]) if a.bodies else {}
    first = p["name"].split()[0]
    for r in reversed(out):
        sender = addr.get(r["sender"])
        who = "me" if sender in me else (first if sender in mine else sender)
        when = datetime.fromtimestamp(r["date_received"], timezone.utc).strftime("%Y-%m-%d")
        others = f" (+{r['n_rcpt'] - 1} others)" if r["n_rcpt"] > 1 else ""
        print(f"{when} {who}: {r['subject'] or '(no subject)'}{others}")
        extra = r["summary"] or ""
        if r["ROWID"] in bodies:
            try:
                extra = email_body(bodies[r["ROWID"]]) or extra
            except Exception:
                pass
        if extra:
            print(f"    {extra.strip()[:400]}")
    if not out:
        print("No email found with this person in Mail.")
