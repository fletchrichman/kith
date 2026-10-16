#!/usr/bin/env python3
"""Personal CRM: a local SQLite record of people, built from Apple Contacts,
Messages, Calendar and (optionally) a LinkedIn Connections.csv export.

Source databases are opened read-only. Message text is never copied into the
CRM; it is read live from Messages when asked for. Everything you add by hand
(notes, relationship labels, cadences, links, merges) survives re-syncs.

Usage: python3 crm.py <command> [args]   (python3 crm.py -h for the list)
"""
import argparse, csv, glob, json, os, re, sqlite3, sys
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("CRM_DB", os.path.join(HERE, "crm.db"))
HOME = os.path.expanduser("~")
MESSAGES_DB = os.environ.get("CRM_MESSAGES_DB", f"{HOME}/Library/Messages/chat.db")
CONTACTS_GLOB = os.environ.get("CRM_CONTACTS_GLOB",
    f"{HOME}/Library/Application Support/AddressBook/**/AddressBook-v22.abcddb")
CALENDAR_DB = os.environ.get("CRM_CALENDAR_DB",
    f"{HOME}/Library/Group Containers/group.com.apple.calendar/Calendar.sqlitedb")
MAIL_GLOB = os.environ.get("CRM_MAIL_GLOB", f"{HOME}/Library/Mail/V*/MailData/Envelope Index")
AUTO_SYNC_HOURS = 6  # read commands refresh first if the last sync is older than this
APPLE_EPOCH = 978307200  # 2001-01-01 in unix seconds

SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  company TEXT, title TEXT,              -- from Apple Contacts, refreshed on sync
  relationship TEXT,                     -- yours: friend, family, work, investor...
  cadence_days INTEGER,                  -- yours: how often you want to be in touch
  snoozed_until TEXT,
  merged_into INTEGER REFERENCES people(id),
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS source_keys (  -- which source records became which person
  source TEXT, key TEXT, person_id INTEGER REFERENCES people(id),
  PRIMARY KEY (source, key)
);
CREATE TABLE IF NOT EXISTS identities (  -- phone numbers, emails, linkedin urls
  kind TEXT, value TEXT, person_id INTEGER REFERENCES people(id),
  source TEXT, manual INTEGER DEFAULT 0,  -- manual=1: you linked it, sync won't move it
  PRIMARY KEY (kind, value)
);
CREATE TABLE IF NOT EXISTS affiliations (
  person_id INTEGER REFERENCES people(id), org TEXT, title TEXT,
  source TEXT, as_of TEXT,               -- as_of: when the source said so, not "true today"
  UNIQUE (person_id, org, source)
);
CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY, person_id INTEGER REFERENCES people(id),
  created_at TEXT DEFAULT (datetime('now')), text TEXT
);
-- Derived from Messages; fully rebuilt on each sync.
CREATE TABLE IF NOT EXISTS msg_stats (
  handle TEXT PRIMARY KEY, dm_in INTEGER, dm_out INTEGER,
  first_at TEXT, last_in_at TEXT, last_out_at TEXT,
  group_msgs INTEGER, group_last_at TEXT
);
CREATE TABLE IF NOT EXISTS group_chats (chat_id INTEGER PRIMARY KEY, name TEXT, last_at TEXT, size INTEGER);
CREATE TABLE IF NOT EXISTS group_members (chat_id INTEGER, handle TEXT, PRIMARY KEY (chat_id, handle));
-- Derived from Calendar; fully rebuilt on each sync.
CREATE TABLE IF NOT EXISTS events (
  event_id TEXT, title TEXT, start_at TEXT, email TEXT, attendee_name TEXT,
  status TEXT,                           -- EventKit participant status: 2 accepted, 3 declined, 4 tentative
  n_attendees INTEGER,                   -- other attendees on the event (excluding me)
  PRIMARY KEY (event_id, start_at, email)
);
-- Derived from Apple Mail's local index (human, non-list mail only); fully rebuilt on each sync.
CREATE TABLE IF NOT EXISTS email_stats (
  address TEXT PRIMARY KEY, n_in INTEGER, n_out INTEGER, last_in_at TEXT, last_out_at TEXT
);
CREATE TABLE IF NOT EXISTS self_addresses (address TEXT PRIMARY KEY, source TEXT);  -- my own emails
CREATE TABLE IF NOT EXISTS sync_log (source TEXT PRIMARY KEY, last_success_at TEXT, detail TEXT);
-- Every merge, so it can be undone: which identities/notes/keys moved from dropped to kept.
CREATE TABLE IF NOT EXISTS merge_log (
  id INTEGER PRIMARY KEY, keep_id INTEGER, drop_id INTEGER, moved_json TEXT, reason TEXT,
  merged_at TEXT DEFAULT (datetime('now')), undone_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_ident_person ON identities(person_id);
CREATE INDEX IF NOT EXISTS ix_aff_org ON affiliations(org);
CREATE INDEX IF NOT EXISTS ix_events_email ON events(email);

-- One row per live person: last observed contact on each channel across all their identities.
DROP VIEW IF EXISTS v_people;
DROP VIEW IF EXISTS v_people_base;
CREATE VIEW v_people_base AS
SELECT p.id, p.name, p.company, p.title, p.relationship, p.cadence_days, p.snoozed_until,
  (SELECT group_concat(i.value, ', ') FROM identities i WHERE i.person_id = p.id) AS identities,
  (SELECT sum(s.dm_in) FROM msg_stats s JOIN identities i ON i.value = s.handle WHERE i.person_id = p.id) AS dm_in,
  (SELECT sum(s.dm_out) FROM msg_stats s JOIN identities i ON i.value = s.handle WHERE i.person_id = p.id) AS dm_out,
  (SELECT max(s.last_in_at) FROM msg_stats s JOIN identities i ON i.value = s.handle WHERE i.person_id = p.id) AS last_in_at,
  (SELECT max(s.last_out_at) FROM msg_stats s JOIN identities i ON i.value = s.handle WHERE i.person_id = p.id) AS last_out_at,
  (SELECT max(max(coalesce(m.last_in_at, ''), coalesce(m.last_out_at, ''))) FROM email_stats m
     JOIN identities i ON i.kind = 'email' AND i.value = m.address WHERE i.person_id = p.id) AS last_email_at,
  (SELECT max(e.start_at) FROM events e JOIN identities i ON i.kind = 'email' AND i.value = e.email
     WHERE i.person_id = p.id AND e.start_at <= datetime('now') AND e.status != '3' AND e.n_attendees <= 10) AS last_event_at,
  (SELECT min(e.start_at) FROM events e JOIN identities i ON i.kind = 'email' AND i.value = e.email
     WHERE i.person_id = p.id AND e.start_at > datetime('now') AND e.status != '3' AND e.n_attendees <= 10) AS next_event_at
FROM people p WHERE p.merged_into IS NULL;
CREATE VIEW v_people AS
SELECT *, nullif(max(coalesce(last_in_at, ''), coalesce(last_out_at, ''), coalesce(nullif(last_email_at, ''), ''),
                     coalesce(last_event_at, '')), '') AS last_contact_at
FROM v_people_base;
"""


# ---------- helpers ----------

def connect():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(SCHEMA)
    return db


def open_ro(path):
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
    except sqlite3.DatabaseError as e:
        raise PermissionError(f"{path}: {e}. Grant Full Disk Access to the app running this "
                              "(System Settings > Privacy & Security > Full Disk Access).")
    return db


def norm_phone(raw):
    if not raw:
        return None
    s = raw.strip()
    digits = re.sub(r"\D", "", s)
    if not digits:
        return None
    if s.startswith("+"):
        return "+" + digits
    if len(digits) == 10:  # assume US/Canada for bare 10-digit numbers
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return "+" + digits if len(digits) > 10 else digits


def norm_email(raw):
    return raw.strip().lower() if raw and "@" in raw else None


def norm_handle(raw):
    return norm_email(raw) if "@" in (raw or "") else norm_phone(raw)


def apple_ts(v):
    """Apple timestamps: seconds (or nanoseconds on newer DBs) since 2001."""
    if v is None:
        return None
    v = float(v)
    if v > 1e12:
        v /= 1e9
    return datetime.fromtimestamp(v + APPLE_EPOCH, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def days_ago(ts):
    if not ts:
        return None
    return (datetime.now(timezone.utc) - datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
            .replace(tzinfo=timezone.utc)).days


def ago(ts):
    d = days_ago(ts)
    return "never" if d is None else f"{ts[:10]} ({d}d ago)"


def norm_name(n):
    return re.sub(r"\s+", " ", (n or "").strip().lower())


def log_sync(db, source, detail):
    db.execute("INSERT OR REPLACE INTO sync_log VALUES (?, datetime('now'), ?)", (source, detail))


def link_identity(db, kind, value, person_id, source):
    """Attach an identity to a person unless it's already attached to someone (first link wins;
    conflicts are left for you to resolve with `merge` or `link`)."""
    if not value:
        return
    row = db.execute("SELECT person_id FROM identities WHERE kind=? AND value=?", (kind, value)).fetchone()
    if row is None:
        db.execute("INSERT INTO identities (kind, value, person_id, source) VALUES (?,?,?,?)",
                   (kind, value, person_id, source))
    elif row["person_id"] is None:
        db.execute("UPDATE identities SET person_id=?, source=? WHERE kind=? AND value=?",
                   (person_id, source, kind, value))


def live(db, pid):
    """Follow merge redirects to the canonical person."""
    while True:
        r = db.execute("SELECT merged_into FROM people WHERE id=?", (pid,)).fetchone()
        if not r or not r["merged_into"]:
            return pid
        pid = r["merged_into"]


def find_people(db, query):
    q = query.strip()
    if q.isdigit():
        return db.execute("SELECT * FROM v_people WHERE id=?", (live(db, int(q)),)).fetchall()
    h = norm_handle(q)
    if h:
        r = db.execute("SELECT person_id FROM identities WHERE value=?", (h,)).fetchone()
        if r and r["person_id"]:
            return db.execute("SELECT * FROM v_people WHERE id=?", (live(db, r["person_id"]),)).fetchall()
    exact = db.execute("SELECT * FROM v_people WHERE lower(name)=?", (norm_name(q),)).fetchall()
    if exact:
        return exact
    like = f"%{q.lower()}%"
    return db.execute("SELECT * FROM v_people WHERE lower(name) LIKE ? ORDER BY name LIMIT 25", (like,)).fetchall()


def one_person(db, query):
    rows = find_people(db, query)
    if not rows:
        sys.exit(f"No person matches '{query}'.")
    if len(rows) > 1:
        print(f"'{query}' is ambiguous; use an id:")
        for r in rows:
            print(f"  [{r['id']}] {r['name']}" + (f" - {r['company']}" if r["company"] else ""))
        sys.exit(1)
    return rows[0]


# ---------- sync: Apple Contacts ----------

def sync_contacts(db):
    files = sorted(set(glob.glob(CONTACTS_GLOB, recursive=True)))
    if not files:
        raise FileNotFoundError(f"No AddressBook databases at {CONTACTS_GLOB} (or no Full Disk Access).")
    seen = 0
    for f in files:
        src = open_ro(f)
        cols = {r[1] for r in src.execute("PRAGMA table_info(ZABCDRECORD)")}
        nick = "ZNICKNAME" if "ZNICKNAME" in cols else "NULL"
        recs = src.execute(f"""SELECT Z_PK, ZUNIQUEID, ZFIRSTNAME, ZLASTNAME, ZORGANIZATION, ZJOBTITLE, {nick} AS nick
            FROM ZABCDRECORD WHERE ZFIRSTNAME IS NOT NULL OR ZLASTNAME IS NOT NULL OR ZORGANIZATION IS NOT NULL""").fetchall()
        phones, emails = {}, {}
        for r in src.execute("SELECT ZOWNER, ZFULLNUMBER FROM ZABCDPHONENUMBER"):
            phones.setdefault(r[0], []).append(norm_phone(r[1]))
        for r in src.execute("SELECT ZOWNER, ZADDRESS FROM ZABCDEMAILADDRESS"):
            emails.setdefault(r[0], []).append(norm_email(r[1]))
        for r in recs:
            name = " ".join(x for x in (r["ZFIRSTNAME"], r["ZLASTNAME"]) if x) or r["nick"] or r["ZORGANIZATION"]
            if not name:
                continue
            idents = [("phone", p) for p in phones.get(r["Z_PK"], []) if p] + \
                     [("email", e) for e in emails.get(r["Z_PK"], []) if e]
            key = r["ZUNIQUEID"] or f"{f}:{r['Z_PK']}"
            pid = upsert_contact(db, key, name, r["ZORGANIZATION"], r["ZJOBTITLE"], idents)
            if r["ZORGANIZATION"] and (r["ZFIRSTNAME"] or r["ZLASTNAME"]):
                db.execute("INSERT OR REPLACE INTO affiliations VALUES (?,?,?,?,date('now'))",
                           (pid, r["ZORGANIZATION"].strip(), r["ZJOBTITLE"], "contacts"))
            seen += 1
        src.close()
    log_sync(db, "contacts", f"{seen} cards from {len(files)} address books")
    return f"contacts: {seen} cards from {len(files)} address books"


def upsert_contact(db, key, name, company, title, idents):
    row = db.execute("SELECT person_id FROM source_keys WHERE source='contacts' AND key=?", (key,)).fetchone()
    if row:
        pid = live(db, row["person_id"])
        db.execute("UPDATE people SET company=coalesce(?, company), title=coalesce(?, title) WHERE id=?",
                   (company, title, pid))
    else:
        # Same card in another account (iCloud vs Google): same name AND a shared phone/email.
        pid = None
        for kind, val in idents:
            m = db.execute("""SELECT p.id, p.name FROM identities i JOIN people p ON p.id=i.person_id
                              WHERE i.kind=? AND i.value=?""", (kind, val)).fetchone()
            if m and norm_name(m["name"]) == norm_name(name):
                pid = live(db, m["id"])
                break
        if pid is None and len(norm_name(name).split()) >= 2:
            # A LinkedIn-only person (no contact card yet) with exactly this full name.
            same = db.execute("""SELECT id FROM people WHERE merged_into IS NULL AND lower(name) = ?
                AND id NOT IN (SELECT person_id FROM source_keys WHERE source = 'contacts')""",
                (norm_name(name),)).fetchall()
            if len(same) == 1:
                pid = same[0]["id"]
        if pid is None:
            pid = db.execute("INSERT INTO people (name, company, title) VALUES (?,?,?)",
                             (name, company, title)).lastrowid
        db.execute("INSERT INTO source_keys VALUES ('contacts', ?, ?)", (key, pid))
    for kind, val in idents:
        link_identity(db, kind, val, pid, "contacts")
    return pid


# ---------- sync: Messages ----------

def decode_attributed_body(blob):
    """Newer macOS stores message text only in an NSAttributedString typedstream."""
    if not blob:
        return None
    try:
        part = blob.split(b"NSString", 1)[1][5:]
        if part[0] == 0x81:
            n = int.from_bytes(part[1:3], "little"); part = part[3:3 + n]
        else:
            n = part[0]; part = part[1:1 + n]
        return part.decode("utf-8", errors="replace")
    except Exception:
        return None


def sync_messages(db):
    src = open_ro(MESSAGES_DB)
    real = "m.associated_message_type = 0 AND coalesce(m.item_type, 0) = 0"  # no reactions / system rows
    dm = src.execute(f"""
        SELECT h.id AS handle,
          sum(m.is_from_me = 0) AS dm_in, sum(m.is_from_me = 1) AS dm_out, min(m.date) AS first,
          max(CASE WHEN m.is_from_me = 0 THEN m.date END) AS last_in,
          max(CASE WHEN m.is_from_me = 1 THEN m.date END) AS last_out
        FROM chat c
        JOIN chat_handle_join chj ON chj.chat_id = c.ROWID
        JOIN handle h ON h.ROWID = chj.handle_id
        JOIN chat_message_join cmj ON cmj.chat_id = c.ROWID
        JOIN message m ON m.ROWID = cmj.message_id
        WHERE c.style = 45 AND {real}
        GROUP BY h.id""").fetchall()
    grp = src.execute(f"""
        SELECT h.id AS handle, count(*) AS n, max(m.date) AS last
        FROM message m JOIN handle h ON h.ROWID = m.handle_id
        JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
        JOIN chat c ON c.ROWID = cmj.chat_id
        WHERE c.style = 43 AND m.is_from_me = 0 AND {real}
        GROUP BY h.id""").fetchall()
    chats = src.execute("""
        SELECT c.ROWID AS id, c.display_name AS name, c.last_read_message_timestamp AS lr,
          (SELECT max(m.date) FROM chat_message_join j JOIN message m ON m.ROWID = j.message_id
             WHERE j.chat_id = c.ROWID) AS last,
          (SELECT count(*) FROM chat_handle_join j WHERE j.chat_id = c.ROWID) AS size
        FROM chat c WHERE c.style = 43""").fetchall()
    members = src.execute("""SELECT j.chat_id, h.id FROM chat_handle_join j JOIN handle h ON h.ROWID = j.handle_id
                             JOIN chat c ON c.ROWID = j.chat_id WHERE c.style = 43""").fetchall()
    src.close()

    stats = {}
    for r in dm:
        h = norm_handle(r["handle"])
        if not h:
            continue
        s = stats.setdefault(h, [0, 0, None, None, None, 0, None])
        s[0] += r["dm_in"] or 0; s[1] += r["dm_out"] or 0
        s[2] = min(filter(None, [s[2], apple_ts(r["first"])]), default=None)
        s[3] = max(filter(None, [s[3], apple_ts(r["last_in"])]), default=None)
        s[4] = max(filter(None, [s[4], apple_ts(r["last_out"])]), default=None)
    for r in grp:
        h = norm_handle(r["handle"])
        if not h:
            continue
        s = stats.setdefault(h, [0, 0, None, None, None, 0, None])
        s[5] += r["n"]; s[6] = max(filter(None, [s[6], apple_ts(r["last"])]), default=None)

    db.execute("DELETE FROM msg_stats"); db.execute("DELETE FROM group_chats"); db.execute("DELETE FROM group_members")
    db.executemany("INSERT INTO msg_stats VALUES (?,?,?,?,?,?,?,?)", [(h, *s) for h, s in stats.items()])
    db.executemany("INSERT INTO group_chats VALUES (?,?,?,?)",
                   [(c["id"], c["name"], apple_ts(c["last"]), c["size"]) for c in chats])
    db.executemany("INSERT OR IGNORE INTO group_members VALUES (?,?)",
                   [(m[0], norm_handle(m[1])) for m in members if norm_handle(m[1])])
    # Make every handle an identity so unknown numbers show up in `unknown`.
    for h in stats:
        db.execute("INSERT OR IGNORE INTO identities (kind, value, source) VALUES (?,?, 'messages')",
                   ("email" if "@" in h else "phone", h))
    detail = f"{len(stats)} handles, {len(chats)} group chats"
    log_sync(db, "messages", detail)
    return "messages: " + detail


# ---------- sync: Calendar ----------

def sync_calendar(db):
    src = open_ro(CALENDAR_DB)
    clean = lambda e: norm_email((e or "").replace("mailto:", ""))
    # My own addresses: participants flagged is_self, plus account names that are emails.
    me = {clean(r[0]) for r in src.execute("""SELECT coalesce(i.address, p.email) FROM Participant p
              LEFT JOIN Identity i ON i.ROWID = p.identity_id WHERE p.is_self = 1""")}
    me |= {clean(r[0].replace("%", "@", 1) if "@" in r[0] else r[0]) for r in src.execute("SELECT name FROM Store") if r[0]}
    me.discard(None)
    db.execute("DELETE FROM self_addresses WHERE source = 'calendar'")
    db.executemany("INSERT OR IGNORE INTO self_addresses VALUES (?, 'calendar')", [(e,) for e in me])
    people = src.execute("""
        SELECT p.owner_id AS id, coalesce(i.address, p.email) AS email, i.display_name AS name, p.status AS status
        FROM Participant p LEFT JOIN Identity i ON i.ROWID = p.identity_id
        WHERE coalesce(p.is_self, 0) = 0""").fetchall()
    attendees = {}
    for r in people:
        e = clean(r["email"])
        if e and e not in me and not e.endswith("calendar.google.com"):  # skip rooms/group calendars
            attendees.setdefault(r["id"], {})[e] = (r["name"], r["status"])
    items = {r["ROWID"]: r for r in src.execute("SELECT ROWID, summary, start_date FROM CalendarItem")
             if r["ROWID"] in attendees}
    # Recurring events: one row per occurrence from Apple's cache (covers roughly +/- 2 years).
    occ = {}
    for r in src.execute("SELECT DISTINCT event_id, occurrence_start_date FROM OccurrenceCache"):
        if r[0] in items:
            occ.setdefault(r[0], set()).add(r[1])
    src.close()
    db.execute("DROP TABLE IF EXISTS events")
    db.executescript(SCHEMA)
    n = 0
    for eid, it in items.items():
        att = attendees[eid]
        for start in occ.get(eid) or {it["start_date"]}:
            for email, (name, status) in att.items():
                db.execute("INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?)",
                           (str(eid), it["summary"], apple_ts(start), email, name, str(status), len(att)))
                n += 1
        for email in att:
            db.execute("INSERT OR IGNORE INTO identities (kind, value, source) VALUES ('email', ?, 'calendar')", (email,))
    detail = f"{len(items)} events with other attendees, {n} attendee-occurrence rows"
    log_sync(db, "calendar", detail)
    return "calendar: " + detail


# ---------- sync: Apple Mail ----------

BOTS = re.compile(r"(no-?reply|do-?not-?reply|notifications?@|mailer-daemon|bounce|@.*\.(mailchimp|sendgrid)|calendar-notification)")


def sync_mail(db):
    """Counts human email per address from Mail's local index (headers only, no bodies).
    Mailing lists and Mail's own 'automated' classification are excluded; Gmail's duplicate
    copies (Inbox + All Mail) count once via the global message id."""
    files = sorted(glob.glob(MAIL_GLOB))
    if not files:
        raise FileNotFoundError("No Apple Mail index found (is Mail set up on this Mac?)")
    src = open_ro(files[-1])
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
    sk = ",".join("?" * len(skip)) or "NULL"
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


# ---------- sync: LinkedIn ----------

def sync_linkedin(db, path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        lines = f.read().splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("First Name"))  # skip LinkedIn's preamble
    as_of = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d")
    created = matched = 0
    for r in csv.DictReader(lines[start:]):
        name = f"{r.get('First Name', '').strip()} {r.get('Last Name', '').strip()}".strip()
        if not name:
            continue
        url = (r.get("URL") or "").strip().rstrip("/").lower() or None
        email = norm_email(r.get("Email Address"))
        pid = None
        for kind, val in (("linkedin", url), ("email", email)):
            if val:
                m = db.execute("SELECT person_id FROM identities WHERE kind=? AND value=? AND person_id IS NOT NULL",
                               (kind, val)).fetchone()
                if m:
                    pid = live(db, m["person_id"]); break
        if pid is None:
            same = db.execute("SELECT id FROM v_people WHERE lower(name)=?", (norm_name(name),)).fetchall()
            if len(same) == 1:  # only a unique exact-name match; otherwise keep an honest duplicate
                pid = same[0]["id"]
        if pid is None:
            pid = db.execute("INSERT INTO people (name) VALUES (?)", (name,)).lastrowid
            created += 1
        else:
            matched += 1
        link_identity(db, "linkedin", url, pid, "linkedin")
        link_identity(db, "email", email, pid, "linkedin")
        if r.get("Company"):
            # A newer export with a different company: keep the old one, marked former.
            db.execute("""UPDATE OR REPLACE affiliations SET source='linkedin (former)'
                WHERE person_id=? AND source='linkedin' AND lower(org) != lower(?)""", (pid, r["Company"].strip()))
            db.execute("INSERT OR REPLACE INTO affiliations VALUES (?,?,?,?,?)",
                       (pid, r["Company"].strip(), (r.get("Position") or "").strip() or None, "linkedin", as_of))
    detail = f"{matched} matched, {created} new (snapshot as of {as_of})"
    log_sync(db, "linkedin", detail)
    return "linkedin: " + detail


# ---------- commands ----------

SYNCERS = dict(contacts=sync_contacts, messages=sync_messages, calendar=sync_calendar, mail=sync_mail)


def auto_sync(db):
    """Refresh quietly if data is stale, so answers are current without a scheduler."""
    if os.environ.get("CRM_NO_AUTOSYNC"):
        return
    r = db.execute("SELECT min(last_success_at) FROM sync_log WHERE source IN ('contacts','messages','calendar')").fetchone()
    if r[0] and r[0] > (datetime.now(timezone.utc) - timedelta(hours=AUTO_SYNC_HOURS)).strftime("%Y-%m-%d %H:%M:%S"):
        return
    failed = []
    for name, fn in SYNCERS.items():
        try:
            fn(db); db.commit()
        except Exception:
            db.rollback(); failed.append(name)
    print(f"(auto-synced; data was older than {AUTO_SYNC_HOURS}h" + (f"; failed: {', '.join(failed)}" if failed else "") + ")",
          file=sys.stderr)


def cmd_me(db, a):
    """Show or add my own email addresses (used to tell sent from received mail)."""
    for e in a.add or []:
        db.execute("INSERT OR IGNORE INTO self_addresses VALUES (?, 'manual')", (norm_email(e),))
    db.commit()
    for r in db.execute("SELECT * FROM self_addresses ORDER BY address"):
        print(f"{r['address']} ({r['source']})")


def cmd_sync(db, a):
    sources = a.sources or list(SYNCERS)
    bad = [s for s in sources if s not in SYNCERS]
    if bad:
        sys.exit(f"unknown source(s): {', '.join(bad)} (choose from {', '.join(SYNCERS)})")
    for s in sources:
        try:
            print(SYNCERS[s](db))
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"{s}: FAILED - {e}")
    if a.linkedin:
        print(sync_linkedin(db, a.linkedin)); db.commit()


def cmd_person(db, a):
    p = one_person(db, a.query)
    pid = p["id"]
    print(f"[{pid}] {p['name']}")
    if p["company"] or p["title"]:
        print(f"  contact card: {', '.join(x for x in (p['title'], p['company']) if x)}")
    if p["relationship"] or p["cadence_days"]:
        print(f"  relationship: {p['relationship'] or '-'}" +
              (f", wants contact every {p['cadence_days']}d" if p["cadence_days"] else ""))
    for r in db.execute("SELECT kind, value, source FROM identities WHERE person_id=? ORDER BY kind", (pid,)):
        s = db.execute("SELECT * FROM msg_stats WHERE handle=?", (r["value"],)).fetchone()
        extra = f"  msgs in/out {s['dm_in']}/{s['dm_out']}, group msgs {s['group_msgs']}" if s else ""
        print(f"  {r['kind']}: {r['value']} ({r['source']}){extra}")
    for r in db.execute("SELECT * FROM affiliations WHERE person_id=?", (pid,)):
        print(f"  works at (per {r['source']}, as of {r['as_of']}): {r['org']}" + (f" - {r['title']}" if r["title"] else ""))
    print(f"  last message from them: {ago(p['last_in_at'])}")
    print(f"  last message to them:   {ago(p['last_out_at'])}")
    em = db.execute("""SELECT sum(n_in), sum(n_out), max(last_in_at), max(last_out_at) FROM email_stats m
        JOIN identities i ON i.kind='email' AND i.value=m.address WHERE i.person_id=?""", (pid,)).fetchone()
    if em[0] or em[1]:
        print(f"  email in/out {em[0]}/{em[1]}; last from them {ago(em[2])}, last to them {ago(em[3])}")
    first = db.execute("""SELECT min(s.first_at) FROM msg_stats s JOIN identities i ON i.value=s.handle
                          WHERE i.person_id=?""", (pid,)).fetchone()[0]
    if first:
        print(f"  first message in history: {first[:10]}")
    groups = db.execute("""SELECT g.chat_id, g.name, g.size, g.last_at FROM group_chats g
        JOIN group_members gm ON gm.chat_id=g.chat_id JOIN identities i ON i.value=gm.handle
        WHERE i.person_id=? GROUP BY g.chat_id ORDER BY g.last_at DESC LIMIT 8""", (pid,)).fetchall()
    if groups:
        print("  shared group chats: " + "; ".join(f"{g['name'] or group_label(db, g['chat_id'], pid)} [{g['size']}]" for g in groups))
    evs = db.execute("""SELECT DISTINCT e.title, e.start_at, e.n_attendees, e.status FROM events e JOIN identities i
        ON i.kind='email' AND i.value=e.email WHERE i.person_id=? ORDER BY e.start_at DESC LIMIT 10""", (pid,)).fetchall()
    for e in evs:
        when = "upcoming" if e["start_at"] > datetime.now(timezone.utc).strftime("%Y-%m-%d") else "calendar"
        extra = " (they declined)" if e["status"] == "3" else ""
        print(f"  {when}: {e['start_at'][:10]} {e['title']} [{e['n_attendees']} others]{extra}")
    for n in db.execute("SELECT created_at, text FROM notes WHERE person_id=? ORDER BY created_at", (pid,)):
        print(f"  note {n['created_at'][:10]}: {n['text']}")


def group_label(db, chat_id, exclude=None):
    """Unnamed group chats: list a few members by name."""
    names = [r[0] for r in db.execute("""SELECT coalesce(p.name, gm.handle) FROM group_members gm
        LEFT JOIN identities i ON i.value = gm.handle LEFT JOIN people p ON p.id = i.person_id
        WHERE gm.chat_id = ? AND coalesce(p.id, -1) != coalesce(?, -2) LIMIT 4""", (chat_id, exclude))]
    return "with " + ", ".join(names) if names else "(unnamed)"


def cmd_grep(db, a):
    """Find conversations mentioning something, across everyone (read live from Messages)."""
    src = open_ro(MESSAGES_DB)
    q = a.text
    variants = {q, q.lower(), q.capitalize(), q.title()}
    ab = " OR ".join("instr(m.attributedBody, CAST(? AS BLOB)) > 0" for _ in variants)
    rows = src.execute(f"""SELECT m.date, m.is_from_me, m.text, m.attributedBody, m.handle_id, c.ROWID AS chat_id,
            c.style, c.display_name FROM message m JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
            JOIN chat c ON c.ROWID = cmj.chat_id
            WHERE m.associated_message_type = 0 AND (m.text LIKE ? OR {ab})
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


def cmd_search(db, a):
    rows = find_people(db, a.query)
    for r in rows:
        last = max(filter(None, [r["last_in_at"], r["last_out_at"]]), default=None)
        print(f"[{r['id']}] {r['name']}" + (f" - {r['company']}" if r["company"] else "") + f"  | last msg {ago(last)}")
    if not rows:
        print("No matches.")


def cmd_at(db, a):
    """Who do I know at X: contact-card orgs, LinkedIn snapshot, and email domains (weak)."""
    q = f"%{a.org.lower()}%"
    rows = db.execute("""SELECT DISTINCT p.id, p.name, af.org, af.title, af.source, af.as_of,
                           max(coalesce(v.last_in_at,''), coalesce(v.last_out_at,'')) AS last
        FROM affiliations af JOIN people p ON p.id = af.person_id JOIN v_people v ON v.id = p.id
        WHERE lower(af.org) LIKE ? ORDER BY last DESC""", (q,)).fetchall()
    for r in rows:
        print(f"[{r['id']}] {r['name']} - {r['title'] or '?'} @ {r['org']} (per {r['source']} as of {r['as_of']}) "
              f"| last msg {ago(r['last'] or None)}")
    dom = re.sub(r"[^a-z0-9]", "", a.org.lower())
    weak = db.execute("""SELECT DISTINCT v.id, v.name, i.value FROM identities i JOIN v_people v ON v.id=i.person_id
        WHERE i.kind='email' AND replace(substr(i.value, instr(i.value,'@')+1), '.', '') LIKE ?
        AND v.id NOT IN (SELECT person_id FROM affiliations WHERE lower(org) LIKE ?)""",
        (f"%{dom}%", q)).fetchall()
    for r in weak:
        print(f"[{r['id']}] {r['name']} - email {r['value']} (domain match only; weak evidence)")
    if not rows and not weak:
        print(f"Nobody found at '{a.org}' in connected sources.")


def cmd_catchup(db, a):
    """People you've labeled (or given a cadence) who are overdue, oldest first."""
    rows = db.execute("""SELECT * FROM v_people WHERE (relationship IS NOT NULL OR cadence_days IS NOT NULL)
        AND (snoozed_until IS NULL OR snoozed_until < date('now'))""").fetchall()
    out = []
    for r in rows:
        if a.label and (r["relationship"] or "").lower() != a.label.lower():
            continue
        last = r["last_contact_at"]
        d = days_ago(last)
        due = r["cadence_days"] or a.default_days
        if d is None or d >= due:
            out.append((d if d is not None else 10**6, r, last, due))
    for d, r, last, due in sorted(out, key=lambda x: -x[0])[: a.limit]:
        nxt = f", upcoming: {r['next_event_at'][:10]}" if r["next_event_at"] else ""
        print(f"[{r['id']}] {r['name']} ({r['relationship'] or '-'}, every {due}d): "
              f"no message/email/meeting found since {ago(last)}{nxt}")
    if not out:
        print("Nobody overdue. (Only people with a relationship label or cadence are considered.)")


def cmd_recent(db, a):
    """Who you've been talking to lately: optional discovery list for labeling people."""
    rows = db.execute("""SELECT * FROM v_people WHERE coalesce(dm_in,0) + coalesce(dm_out,0) > 0
        ORDER BY max(coalesce(last_in_at,''), coalesce(last_out_at,'')) DESC LIMIT ?""", (a.limit,)).fetchall()
    for r in rows:
        last = max(filter(None, [r["last_in_at"], r["last_out_at"]]))
        print(f"[{r['id']}] {r['name']} ({r['relationship'] or 'unlabeled'}) msgs {r['dm_in']}/{r['dm_out']} last {ago(last)}")


def cmd_top(db, a):
    rows = db.execute("""SELECT * FROM v_people WHERE coalesce(dm_in,0) > 0 AND coalesce(dm_out,0) > 0
        ORDER BY dm_in + dm_out DESC LIMIT ?""", (a.limit,)).fetchall()
    for r in rows:
        last = max(filter(None, [r["last_in_at"], r["last_out_at"]]))
        print(f"[{r['id']}] {r['name']} ({r['relationship'] or 'unlabeled'}) msgs {r['dm_in']}/{r['dm_out']} last {ago(last)}")


def cmd_unknown(db, a):
    rows = db.execute("""SELECT s.* FROM msg_stats s JOIN identities i ON i.value = s.handle
        WHERE i.person_id IS NULL AND s.dm_in > 0 AND s.dm_out > 0
          AND (s.handle LIKE '%@%' OR length(s.handle) >= 8)  -- skip business short codes
        ORDER BY s.dm_in + s.dm_out DESC LIMIT ?""", (a.limit,)).fetchall()
    for r in rows:
        print(f"{r['handle']}: msgs {r['dm_in']}/{r['dm_out']}, last {ago(max(filter(None, [r['last_in_at'], r['last_out_at']])))}")
    if not rows:
        print("No unknown two-way handles.")


def cmd_messages(db, a):
    """Read message text live from Messages (never stored in the CRM)."""
    p = one_person(db, a.query)
    handles = [r[0] for r in db.execute("SELECT value FROM identities WHERE person_id=? AND kind IN ('phone','email')", (p["id"],))]
    if not handles:
        sys.exit("No phone/email for this person.")
    src = open_ro(MESSAGES_DB)
    ids = [r["ROWID"] for r in src.execute("SELECT ROWID, id FROM handle") if norm_handle(r["id"]) in handles]
    if not ids:
        sys.exit("No Messages history for this person's numbers/emails.")
    qs = ",".join("?" * len(ids))
    where = "c.style = 45" if not a.groups else "1=1"
    rows = src.execute(f"""
        SELECT m.date, m.is_from_me, m.text, m.attributedBody, c.style, c.display_name
        FROM message m JOIN chat_message_join cmj ON cmj.message_id = m.ROWID JOIN chat c ON c.ROWID = cmj.chat_id
        WHERE {where} AND m.associated_message_type = 0 AND (
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
    root = os.path.dirname(os.path.dirname(sorted(glob.glob(MAIL_GLOB))[-1]))
    for d, _, files in os.walk(root):
        for f in files:
            if f.endswith(".emlx"):
                rid = f.split(".")[0]
                if rid in want:
                    found[int(rid)] = os.path.join(d, f)
        if len(found) == len(want):
            break
    return found


def cmd_emails(db, a):
    """Recent email with a person: subjects (and optionally opening lines), read live from Mail."""
    p = one_person(db, a.query)
    mine = {r[0] for r in db.execute("SELECT value FROM identities WHERE person_id=? AND kind='email'", (p["id"],))}
    if not mine:
        sys.exit("No email address for this person.")
    me = {r[0] for r in db.execute("SELECT address FROM self_addresses")}
    src = open_ro(sorted(glob.glob(MAIL_GLOB))[-1])
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


def cmd_note(db, a):
    p = one_person(db, a.query)
    db.execute("INSERT INTO notes (person_id, text) VALUES (?,?)", (p["id"], a.text)); db.commit()
    print(f"Noted on {p['name']}.")


def cmd_set(db, a):
    p = one_person(db, a.query)
    fields = {"relationship": a.relationship, "cadence_days": a.cadence, "snoozed_until": a.snooze,
              "name": a.name, "company": a.company, "title": a.title}
    for k, v in fields.items():
        if v is not None:
            db.execute(f"UPDATE people SET {k}=? WHERE id=?", (None if v in ("", "none") else v, p["id"]))
    db.commit()
    print(f"Updated {p['name']}.")


def cmd_add(db, a):
    pid = db.execute("INSERT INTO people (name, company, title, relationship) VALUES (?,?,?,?)",
                     (a.name, a.company, a.title, a.relationship)).lastrowid
    for h in a.handle or []:
        v = norm_handle(h)
        db.execute("""INSERT INTO identities (kind, value, person_id, source, manual) VALUES (?,?,?, 'manual', 1)
            ON CONFLICT(kind, value) DO UPDATE SET person_id=excluded.person_id, manual=1""",
                   ("email" if "@" in v else "phone", v, pid))
    db.commit()
    print(f"Added [{pid}] {a.name}.")


def cmd_link(db, a):
    v = norm_handle(a.handle) or a.handle.strip().lower()
    p = one_person(db, a.query)
    kind = "email" if "@" in v else ("linkedin" if "linkedin.com" in v else "phone")
    db.execute("""INSERT INTO identities (kind, value, person_id, source, manual) VALUES (?,?,?, 'manual', 1)
        ON CONFLICT(kind, value) DO UPDATE SET person_id=excluded.person_id, manual=1""", (kind, v, p["id"]))
    db.commit()
    print(f"Linked {v} -> {p['name']}.")


def merge_people(db, keep_id, drop_id, reason):
    keep = db.execute("SELECT * FROM people WHERE id=?", (keep_id,)).fetchone()
    drop = db.execute("SELECT * FROM people WHERE id=?", (drop_id,)).fetchone()
    moved = {
        "identities": [[r[0], r[1]] for r in db.execute("SELECT kind, value FROM identities WHERE person_id=?", (drop_id,))],
        "notes": [r[0] for r in db.execute("SELECT id FROM notes WHERE person_id=?", (drop_id,))],
        "source_keys": [[r[0], r[1]] for r in db.execute("SELECT source, key FROM source_keys WHERE person_id=?", (drop_id,))],
        "affiliations": [[r[0], r[1]] for r in db.execute("SELECT org, source FROM affiliations WHERE person_id=?", (drop_id,))],
        "fields": {k: drop[k] for k in ("relationship", "cadence_days", "company", "title")},
    }
    for t in ("identities", "notes", "source_keys"):
        db.execute(f"UPDATE {t} SET person_id=? WHERE person_id=?", (keep_id, drop_id))
    db.execute("UPDATE OR IGNORE affiliations SET person_id=? WHERE person_id=?", (keep_id, drop_id))
    db.execute("""UPDATE people SET relationship=coalesce(relationship, ?), cadence_days=coalesce(cadence_days, ?),
                  company=coalesce(company, ?), title=coalesce(title, ?) WHERE id=?""",
               (drop["relationship"], drop["cadence_days"], drop["company"], drop["title"], keep_id))
    db.execute("UPDATE people SET merged_into=? WHERE id=?", (keep_id, drop_id))
    db.execute("INSERT INTO merge_log (keep_id, drop_id, moved_json, reason) VALUES (?,?,?,?)",
               (keep_id, drop_id, json.dumps(moved), reason))
    return keep, drop


def cmd_merge(db, a):
    keep, drop = one_person(db, a.keep), one_person(db, a.drop)
    if keep["id"] == drop["id"]:
        sys.exit("Same person.")
    merge_people(db, keep["id"], drop["id"], "manual")
    db.commit()
    print(f"Merged [{drop['id']}] {drop['name']} into [{keep['id']}] {keep['name']}. Undo: unmerge {drop['id']}")


def cmd_unmerge(db, a):
    """Reverse a merge: the dropped person comes back with what they brought."""
    m = db.execute("SELECT * FROM merge_log WHERE drop_id=? AND undone_at IS NULL ORDER BY id DESC LIMIT 1",
                   (a.id,)).fetchone()
    if not m:
        sys.exit(f"No active merge for person {a.id}.")
    moved = json.loads(m["moved_json"])
    for kind, value in moved["identities"]:
        db.execute("UPDATE identities SET person_id=? WHERE kind=? AND value=? AND person_id=?",
                   (m["drop_id"], kind, value, m["keep_id"]))
    for nid in moved["notes"]:
        db.execute("UPDATE notes SET person_id=? WHERE id=?", (m["drop_id"], nid))
    for src, key in moved["source_keys"]:
        db.execute("UPDATE source_keys SET person_id=? WHERE source=? AND key=?", (m["drop_id"], src, key))
    for org, src in moved["affiliations"]:
        db.execute("UPDATE OR IGNORE affiliations SET person_id=? WHERE person_id=? AND org=? AND source=?",
                   (m["drop_id"], m["keep_id"], org, src))
    db.execute("UPDATE people SET merged_into=NULL WHERE id=?", (m["drop_id"],))
    db.execute("UPDATE merge_log SET undone_at=datetime('now') WHERE id=?", (m["id"],))
    db.commit()
    print(f"Unmerged person {m['drop_id']} from {m['keep_id']}.")


def dupe_groups(db):
    """Same exact name, two or more live people."""
    out = []
    for g in db.execute("""SELECT lower(name) n, group_concat(id) ids FROM v_people
                           GROUP BY lower(name) HAVING count(*) > 1 ORDER BY n"""):
        out.append([db.execute("""SELECT v.*, (SELECT group_concat(DISTINCT source) FROM identities WHERE person_id=v.id) srcs,
            (SELECT count(*) FROM source_keys WHERE person_id=v.id AND source='contacts') cards,
            (SELECT group_concat(DISTINCT org) FROM affiliations WHERE person_id=v.id) orgs
            FROM v_people v WHERE v.id=?""", (int(i),)).fetchone() for i in g["ids"].split(",")])
    return out


def safe_to_merge(group):
    """Conservative auto-merge rule: a full name (2+ words) shared by exactly two people, where
    at most one has a contact card (i.e. a LinkedIn/manual row next to a card), or both are cards
    whose known employers don't disagree. Single names ('Albert') are never auto-merged."""
    if len(group) != 2 or len(group[0]["name"].split()) < 2:
        return False
    a, b = group
    if a["cards"] and b["cards"]:
        oa = {o.lower() for o in (a["orgs"] or "").split(",") if o}
        ob = {o.lower() for o in (b["orgs"] or "").split(",") if o}
        return not (oa and ob and not (oa & ob))
    return True


def cmd_dupes(db, a):
    """Same-name people with the evidence for each, so a merge decision is quick."""
    groups = dupe_groups(db)
    if a.auto:
        n = 0
        for g in groups:
            if safe_to_merge(g):
                keep, drop = sorted(g, key=lambda r: (-(r["cards"] or 0), -((r["dm_in"] or 0) + (r["dm_out"] or 0)), r["id"]))
                merge_people(db, keep["id"], drop["id"], "auto: same full name, no conflicting employer")
                n += 1
        db.commit()
        print(f"Auto-merged {n} pairs (undo any with: unmerge <id>). {len(groups) - n} groups left for review.")
        groups = dupe_groups(db)
    for g in groups[: a.limit]:
        print(g[0]["name"])
        for r in g:
            last = r["last_contact_at"]
            print(f"  [{r['id']}] cards={r['cards']} sources={r['srcs'] or '-'} orgs={r['orgs'] or '-'} "
                  f"msgs={(r['dm_in'] or 0) + (r['dm_out'] or 0)} last={last[:10] if last else 'never'}")
    if not groups:
        print("No same-name duplicates.")


def cmd_status(db, a):
    for r in db.execute("SELECT * FROM sync_log ORDER BY source"):
        print(f"{r['source']}: last success {r['last_success_at']} UTC - {r['detail']}")
    c = db.execute("""SELECT (SELECT count(*) FROM v_people), (SELECT count(*) FROM identities WHERE person_id IS NULL),
        (SELECT min(first_at) FROM msg_stats), (SELECT max(max(coalesce(last_in_at,'')), max(coalesce(last_out_at,''))) FROM msg_stats)""").fetchone()
    print(f"{c[0]} people, {c[1]} unlinked identities. Messages history on this Mac spans {c[2] or '?'} to {c[3] or '?'}.")
    print("Note: Messages only covers what's stored on this Mac; with Messages in iCloud older history may be missing.")


def cmd_sql(db, a):
    ro = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    cur = ro.execute(a.query)
    cols = [d[0] for d in cur.description or []]
    print(" | ".join(cols))
    for row in cur.fetchmany(a.limit):
        print(" | ".join("" if v is None else str(v) for v in row))


def main():
    ap = argparse.ArgumentParser(description="kith: your personal CRM")
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("sync", help="import from Contacts, Messages, Calendar (+ --linkedin CSV)")
    # No argparse choices: Python < 3.12 rejects an empty nargs="*" list against them; cmd_sync validates.
    s.add_argument("sources", nargs="*", metavar="{contacts,messages,calendar,mail}")
    s.add_argument("--linkedin", help="path to LinkedIn Connections.csv")
    s.set_defaults(f=cmd_sync)
    s = sp.add_parser("person", help="everything about one person"); s.add_argument("query"); s.set_defaults(f=cmd_person)
    s = sp.add_parser("search", help="find people by name/phone/email"); s.add_argument("query"); s.set_defaults(f=cmd_search)
    s = sp.add_parser("at", help="who do I know at an organization"); s.add_argument("org"); s.set_defaults(f=cmd_at)
    s = sp.add_parser("catchup", help="labeled people you haven't been in touch with")
    s.add_argument("--label"); s.add_argument("--default-days", type=int, default=60); s.add_argument("--limit", type=int, default=25)
    s.set_defaults(f=cmd_catchup)
    s = sp.add_parser("recent", help="people you've messaged recently"); s.add_argument("--limit", type=int, default=30); s.set_defaults(f=cmd_recent)
    s = sp.add_parser("top", help="people you message most (all time)"); s.add_argument("--limit", type=int, default=50); s.set_defaults(f=cmd_top)
    s = sp.add_parser("unknown", help="frequent two-way handles not in your contacts"); s.add_argument("--limit", type=int, default=30); s.set_defaults(f=cmd_unknown)
    s = sp.add_parser("messages", help="read recent message text with a person (live from Messages)")
    s.add_argument("query"); s.add_argument("--limit", type=int, default=40); s.add_argument("--grep")
    s.add_argument("--groups", action="store_true", help="include their messages in group chats")
    s.set_defaults(f=cmd_messages)
    s = sp.add_parser("grep", help="who did I talk to about X (searches all messages live)")
    s.add_argument("text"); s.add_argument("--limit", type=int, default=30); s.set_defaults(f=cmd_grep)
    s = sp.add_parser("emails", help="recent email with a person (subjects; --bodies for opening lines), live from Mail")
    s.add_argument("query"); s.add_argument("--limit", type=int, default=25); s.add_argument("--grep")
    s.add_argument("--bodies", action="store_true"); s.set_defaults(f=cmd_emails)
    s = sp.add_parser("note", help="add a note to a person"); s.add_argument("query"); s.add_argument("text"); s.set_defaults(f=cmd_note)
    s = sp.add_parser("set", help="set relationship/cadence/snooze/name/company/title")
    s.add_argument("query"); s.add_argument("--relationship"); s.add_argument("--cadence", type=int)
    s.add_argument("--snooze", help="YYYY-MM-DD"); s.add_argument("--name"); s.add_argument("--company"); s.add_argument("--title")
    s.set_defaults(f=cmd_set)
    s = sp.add_parser("add", help="add a person manually"); s.add_argument("name"); s.add_argument("--handle", action="append")
    s.add_argument("--company"); s.add_argument("--title"); s.add_argument("--relationship"); s.set_defaults(f=cmd_add)
    s = sp.add_parser("link", help="attach a phone/email/linkedin url to a person"); s.add_argument("handle"); s.add_argument("query"); s.set_defaults(f=cmd_link)
    s = sp.add_parser("merge", help="merge DROP into KEEP"); s.add_argument("keep"); s.add_argument("drop"); s.set_defaults(f=cmd_merge)
    s = sp.add_parser("dupes", help="same-name people to review (--auto merges the safe ones)")
    s.add_argument("--limit", type=int, default=50); s.add_argument("--auto", action="store_true"); s.set_defaults(f=cmd_dupes)
    s = sp.add_parser("unmerge", help="undo a merge"); s.add_argument("id", type=int); s.set_defaults(f=cmd_unmerge)
    s = sp.add_parser("me", help="list/add my own email addresses"); s.add_argument("--add", action="append"); s.set_defaults(f=cmd_me)
    s = sp.add_parser("status", help="source freshness and coverage"); s.set_defaults(f=cmd_status)
    s = sp.add_parser("sql", help="read-only SQL against crm.db"); s.add_argument("query"); s.add_argument("--limit", type=int, default=200); s.set_defaults(f=cmd_sql)
    a = ap.parse_args()
    db = connect()
    if a.cmd not in ("sync", "sql"):
        auto_sync(db)
    a.f(db, a)


if __name__ == "__main__":
    main()
