-- crm.db schema as of the first release (9b9c6ac), before PRAGMA user_version migrations.
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
