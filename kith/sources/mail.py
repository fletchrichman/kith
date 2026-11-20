"""Apple Mail: per-address counts from Mail's local index, and live reads of .emlx bodies."""
import glob, os, re
from datetime import datetime, timezone

from kith.config import MAIL_GLOB
from kith.db import link_identity, log_sync
from kith.sources.base import open_source
from kith.util import norm_email, norm_name


def mail_index():
    """Mail's Envelope Index for the newest Mail data version (V10 over V9; not a string sort,
    which would pick V9). Older versions are left behind by macOS upgrades and go stale."""
    files = glob.glob(MAIL_GLOB)
    if not files:
        raise FileNotFoundError("No Apple Mail index found (is Mail set up on this Mac, "
                                "and does this app have Full Disk Access?)")
    def version(path):
        m = re.search(r"/V(\d+)/", path)
        return (int(m.group(1)) if m else -1, os.path.getmtime(path))
    return max(files, key=version)


BOTS = re.compile(r"(no-?reply|do-?not-?reply|notifications?@|mailer-daemon|bounce|@.*\.(mailchimp|sendgrid)|calendar-notification)")


def sync_mail(db):
    """Counts human email per address from Mail's local index (headers only, no bodies).
    Mailing lists and Mail's own 'automated' classification are excluded; Gmail's duplicate
    copies (Inbox + All Mail) count once via the global message id."""
    src = open_source(mail_index(), "Mail")
    # Gmail keeps sent mail in "All Mail", so direction comes from the sender being one of my
    # addresses (from Calendar accounts, `me add`, and senders found in any Sent folder).
    me = {r[0] for r in db.execute("SELECT address FROM self_addresses")}
    me |= {norm_email(r[0]) for r in src.execute("""SELECT DISTINCT a.address FROM messages m
          JOIN addresses a ON a.ROWID = m.sender JOIN mailboxes mb ON mb.ROWID = m.mailbox
          WHERE lower(mb.url) LIKE '%sent%'""")}
    me.discard(None)
    skip = [r[0] for r in src.execute("""SELECT ROWID FROM mailboxes WHERE lower(url) LIKE '%spam%'
            OR lower(url) LIKE '%junk%' OR lower(url) LIKE '%trash%' OR lower(url) LIKE '%deleted%'
            OR lower(url) LIKE '%draft%'""")]
    sk = ",".join("?" * len(skip)) or "-1"  # not NULL: "x NOT IN (NULL)" is never true
    human = "coalesce(m.list_id_hash, 0) = 0 AND coalesce(m.automated_conversation, 0) = 0 AND coalesce(m.deleted, 0) = 0"
    stats = {}
    senders = src.execute(f"""SELECT a.address, count(DISTINCT coalesce(m.global_message_id, m.ROWID)) n,
            max(m.date_received) last FROM messages m JOIN addresses a ON a.ROWID = m.sender
            WHERE m.mailbox NOT IN ({sk}) AND {human} GROUP BY a.address""", skip).fetchall()
    my_ids = [r[0] for r in src.execute("SELECT ROWID, address FROM addresses") if norm_email(r[1]) in me]
    for r in senders:
        e = norm_email(r[0])
        if e and e not in me and not BOTS.search(e):
            s_ = stats.setdefault(e, [0, 0, None, None]); s_[0] += r[1]; s_[2] = max(filter(None, [s_[2], r[2]]), default=None)
    mq = ",".join("?" * len(my_ids)) or "NULL"
    for r in src.execute(f"""SELECT a.address, count(DISTINCT coalesce(m.global_message_id, m.ROWID)) n,
            max(m.date_sent) last FROM messages m JOIN recipients rc ON rc.message = m.ROWID
            JOIN addresses a ON a.ROWID = rc.address
            WHERE m.sender IN ({mq}) AND m.mailbox NOT IN ({sk}) GROUP BY a.address""", (*my_ids, *skip)):
        e = norm_email(r[0])
        if e and e not in me:
            s_ = stats.setdefault(e, [0, 0, None, None]); s_[1] += r[1]; s_[3] = max(filter(None, [s_[3], r[2]]), default=None)
    names = {}
    for r in src.execute("SELECT address, comment FROM addresses WHERE comment IS NOT NULL AND comment != ''"):
        e = norm_email(r[0])
        if e in stats:
            names.setdefault(e, r[1].strip().strip('"\''))
    src.close()
    ts = lambda v: datetime.fromtimestamp(v, timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if v else None
    db.execute("DELETE FROM email_stats")
    db.executemany("INSERT INTO email_stats VALUES (?,?,?,?,?)",
                   [(e, a, b, ts(c), ts(d)) for e, (a, b, c, d) in stats.items()])
    # Only addresses you've written to become identities; inbound-only senders are mostly noise.
    # Two-way correspondents not in Contacts become people, named from the email header.
    created = 0
    for e, (n_in, n_out, _, _) in stats.items():
        if not n_out:
            continue
        db.execute("INSERT OR IGNORE INTO identities (kind, value, source) VALUES ('email', ?, 'mail')", (e,))
        linked = db.execute("SELECT person_id FROM identities WHERE kind='email' AND value=?", (e,)).fetchone()[0]
        name = names.get(e, "")
        if linked or not n_in or "@" in name or len(name.split()) < 2:
            continue
        same = db.execute("SELECT id FROM v_people WHERE lower(name)=?", (norm_name(name),)).fetchall()
        if len(same) == 1:
            pid = same[0]["id"]
        else:
            pid = db.execute("INSERT INTO people (name) VALUES (?)", (name,)).lastrowid
            created += 1
        db.execute("INSERT OR IGNORE INTO source_keys VALUES ('mail', ?, ?)", (e, pid))
        link_identity(db, "email", e, pid, "mail")
    detail = (f"{len(stats)} human correspondents, {sum(1 for v in stats.values() if v[1])} you've written to, "
              f"{created} new people")
    log_sync(db, "mail", detail)
    return "mail: " + detail


def email_body(path, limit=400):
    """First few hundred characters of the plain-text part of a Mail .emlx file."""
    import email
    from email import policy
    with open(path, "rb") as f:
        n = int(f.readline().strip() or 0)
        msg = email.message_from_bytes(f.read(n) if n else f.read(), policy=policy.default)
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    text = part.get_content()
    if part.get_content_type() == "text/html":
        text = re.sub(r"<[^>]+>", " ", text)
    lines = [l for l in text.splitlines() if l.strip() and not l.startswith(">")]
    cut = []
    for l in lines:  # stop at the quoted reply ("On Tue, X wrote:")
        if re.match(r"^\s*On .+wrote:\s*$", l) or l.strip().startswith("From:"):
            break
        cut.append(l.strip())
    return re.sub(r"\s+", " ", " ".join(cut))[:limit]


def find_emlx(rowids):
    """Locate .emlx files for the given Mail message ids (one walk of the Mail folder)."""
    want, found = {str(r) for r in rowids}, {}
    root = os.path.dirname(os.path.dirname(mail_index()))
    for d, _, files in os.walk(root):
        for f in files:
            if f.endswith(".emlx"):
                rid = f.split(".")[0]
                if rid in want:
                    found[int(rid)] = os.path.join(d, f)
        if len(found) == len(want):
            break
    return found
