"""crm.db: schema, migrations, sync bookkeeping and person lookup."""
import os, sqlite3, sys

from kith.config import DB_PATH
from kith.util import norm_handle, norm_name


# Current schema, for new databases. Existing ones are brought up to date by MIGRATIONS below.
TABLES = """
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
CREATE TABLE IF NOT EXISTS sync_log (
  source TEXT PRIMARY KEY, last_success_at TEXT, detail TEXT,
  last_attempt_at TEXT, last_error TEXT, last_error_at TEXT  -- last_error is cleared by the next success
);
-- Every merge, so it can be undone: which identities/notes/keys moved from dropped to kept.
CREATE TABLE IF NOT EXISTS merge_log (
  id INTEGER PRIMARY KEY, keep_id INTEGER, drop_id INTEGER, moved_json TEXT, reason TEXT,
  merged_at TEXT DEFAULT (datetime('now')), undone_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_ident_person ON identities(person_id);
CREATE INDEX IF NOT EXISTS ix_aff_org ON affiliations(org);
CREATE INDEX IF NOT EXISTS ix_events_email ON events(email);
"""


VIEWS = """
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


def add_column(db, table, col, decl):
    if col not in {r[1] for r in db.execute(f"PRAGMA table_info({table})")}:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def m1_sync_errors(db):
    for col in ("last_attempt_at", "last_error", "last_error_at"):
        add_column(db, "sync_log", col, "TEXT")
    os.chmod(DB_PATH, 0o600)  # databases created before this tightening was added


# Append-only: each step brings PRAGMA user_version from its index to index + 1. Steps must be
# idempotent, because new databases already have the latest TABLES and run every step once.
MIGRATIONS = [m1_sync_errors]


def connect():
    if not os.path.exists(DB_PATH):  # create it owner-only before SQLite writes anything into it
        os.makedirs(os.path.dirname(DB_PATH) or ".", mode=0o700, exist_ok=True)
        os.close(os.open(DB_PATH, os.O_CREAT | os.O_WRONLY, 0o600))
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(TABLES)
    version = db.execute("PRAGMA user_version").fetchone()[0]
    for i, step in enumerate(MIGRATIONS[version:], version + 1):
        step(db)
        db.execute(f"PRAGMA user_version = {i}")
        db.commit()
    db.executescript(VIEWS)
    return db


def log_sync(db, source, detail):
    db.execute("""INSERT INTO sync_log (source, last_success_at, detail, last_attempt_at)
        VALUES (?, datetime('now'), ?, datetime('now'))
        ON CONFLICT(source) DO UPDATE SET last_success_at=excluded.last_success_at, detail=excluded.detail,
          last_attempt_at=excluded.last_attempt_at, last_error=NULL, last_error_at=NULL""", (source, detail))


def log_failure(db, source, err):
    """Record why a source failed (after rolling back its partial work), keeping the last success."""
    db.rollback()
    msg = str(err) or type(err).__name__
    db.execute("""INSERT INTO sync_log (source, last_attempt_at, last_error, last_error_at)
        VALUES (?, datetime('now'), ?, datetime('now'))
        ON CONFLICT(source) DO UPDATE SET last_attempt_at=excluded.last_attempt_at,
          last_error=excluded.last_error, last_error_at=excluded.last_error_at""", (source, msg))
    db.commit()
    return msg


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
    if q.isdigit():  # an id, unless no such person (then it may be a phone number typed as digits)
        rows = db.execute("SELECT * FROM v_people WHERE id=?", (live(db, int(q)),)).fetchall()
        if rows:
            return rows
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
