"""Builds small, entirely fake copies of the Apple databases (and a LinkedIn export) that crm.py reads.

Schemas mimic the real ones closely enough for crm.py's queries and format probes: table and
column names, Apple's timestamp epochs (Messages in nanoseconds, Calendar in seconds since 2001,
Mail in unix seconds), the typedstream attributedBody of newer Messages, multiple AddressBook
sources, multiple Mail data versions and .emlx files. Timestamps are relative to "now" so the
recency logic (catchup, upcoming meetings, doctor's history checks) behaves the same any day.

Usage: python3 tests/fixtures/build.py <dest-dir>   (prints the CRM_* environment to use)
"""
import os
import sqlite3
import sys
import time

APPLE_EPOCH = 978307200
DAY = 86400


def apple_s(days):
    return time.time() + days * DAY - APPLE_EPOCH


def apple_ns(days):
    return int(apple_s(days) * 1e9)


def unix(days):
    return int(time.time() + days * DAY)


def typedstream(text):
    """An NSAttributedString archive the way newer Messages stores message text."""
    raw = text.encode("utf-8")
    n = bytes([len(raw)]) if len(raw) < 0x81 else b"\x81" + len(raw).to_bytes(2, "little")
    return (b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00\x84\x84\x08"
            b"NSObject\x00\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+" + n + raw + b"\x86\x84\x02iI\x01")


def make(path, schema):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        os.remove(path)
    db = sqlite3.connect(path)
    db.executescript(schema)
    return db


# ---------- Contacts ----------

CONTACTS_SCHEMA = """
CREATE TABLE ZABCDRECORD (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER, ZUNIQUEID VARCHAR,
  ZFIRSTNAME VARCHAR, ZLASTNAME VARCHAR, ZMIDDLENAME VARCHAR, ZNICKNAME VARCHAR, ZORGANIZATION VARCHAR,
  ZDEPARTMENT VARCHAR, ZJOBTITLE VARCHAR, ZCREATIONDATE TIMESTAMP, ZMODIFICATIONDATE TIMESTAMP);
CREATE TABLE ZABCDPHONENUMBER (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER, ZOWNER INTEGER,
  ZISPRIMARY INTEGER, ZORDERINGINDEX INTEGER, ZLABEL VARCHAR, ZFULLNUMBER VARCHAR, ZAREACODE VARCHAR);
CREATE TABLE ZABCDEMAILADDRESS (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER, ZOWNER INTEGER,
  ZISPRIMARY INTEGER, ZORDERINGINDEX INTEGER, ZLABEL VARCHAR, ZADDRESS VARCHAR, ZADDRESSNORMALIZED VARCHAR);
"""

# (unique id, first, last, nickname, org, title, phones, emails)
CONTACTS_ICLOUD = [
    ("A1:ABPerson", "Alice", "Anders", None, "Acme Corp", "Engineer", ["(415) 555-0101"], ["Alice@Acme.example"]),
    ("A2:ABPerson", "Bob", "Baker", None, "Globex", "Product Manager", ["+1 415-555-0102"], ["bob@globex.example"]),
    ("A3:ABPerson", "Carol", "Chen", None, None, None, ["415.555.0103"], ["carol@personal.example"]),
    ("A4:ABPerson", "Dave", "Dupe", None, "Initech", None, ["415-555-0141"], ["dave@initech.example"]),
    ("A5:ABPerson", None, None, None, "Acme Corp", None, ["+1 (415) 555-0199"], []),
    ("A6:ABPerson", "Albert", None, None, None, None, ["415-555-0161"], []),
    ("A7:ABPerson", "Ivy", "Irwin", None, None, None, ["415-555-0171"], []),
    ("A8:ABPerson", None, None, "Nobody", None, None, ["415-555-0181"], []),  # no name or org: skipped
]
CONTACTS_GOOGLE = [
    ("G1:ABPerson", "Alice", "Anders", None, None, None, ["+14155550101"], ["alice.personal@home.example"]),
    ("G2:ABPerson", "Dave", "Dupe", None, "Hooli", None, ["415-555-0142"], ["dave@hooli.example"]),
    ("G3:ABPerson", "Erin", "Evans", None, "Initrode", "VP Sales", [], ["erin@initrode.example"]),
    ("G4:ABPerson", "Albert", None, None, None, None, ["415-555-0162"], []),
    ("G5:ABPerson", "Ivy", "Irwin", None, None, None, ["415-555-0172"], []),
]


def build_contacts(root):
    books = [(os.path.join(root, "AddressBook-v22.abcddb"), CONTACTS_ICLOUD),
             (os.path.join(root, "Sources", "5F3C0D2E-FAKE-SOURCE", "AddressBook-v22.abcddb"), CONTACTS_GOOGLE)]
    for path, cards in books:
        db = make(path, CONTACTS_SCHEMA)
        for pk, (uid, first, last, nick, org, title, phones, emails) in enumerate(cards, 1):
            db.execute("INSERT INTO ZABCDRECORD (Z_PK, Z_ENT, ZUNIQUEID, ZFIRSTNAME, ZLASTNAME, ZNICKNAME, "
                       "ZORGANIZATION, ZJOBTITLE) VALUES (?,22,?,?,?,?,?,?)", (pk, uid, first, last, nick, org, title))
            for p in phones:
                db.execute("INSERT INTO ZABCDPHONENUMBER (ZOWNER, ZLABEL, ZFULLNUMBER) VALUES (?, '_$!<Mobile>!$_', ?)",
                           (pk, p))
            for e in emails:
                db.execute("INSERT INTO ZABCDEMAILADDRESS (ZOWNER, ZLABEL, ZADDRESS) VALUES (?, '_$!<Work>!$_', ?)",
                           (pk, e))
        db.commit()
        db.close()
    return os.path.join(root, "**", "AddressBook-v22.abcddb")


# ---------- Messages ----------

MESSAGES_SCHEMA = """
CREATE TABLE handle (ROWID INTEGER PRIMARY KEY AUTOINCREMENT UNIQUE, id TEXT NOT NULL, country TEXT,
  service TEXT NOT NULL, uncanonicalized_id TEXT, person_centric_id TEXT, UNIQUE (id, service));
CREATE TABLE chat (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, guid TEXT UNIQUE NOT NULL, style INTEGER,
  state INTEGER, account_id TEXT, chat_identifier TEXT, service_name TEXT, room_name TEXT,
  display_name TEXT, group_id TEXT, is_archived INTEGER DEFAULT 0);
CREATE TABLE message (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, guid TEXT UNIQUE NOT NULL, text TEXT,
  handle_id INTEGER DEFAULT 0, service TEXT, date INTEGER, date_read INTEGER, date_delivered INTEGER,
  is_from_me INTEGER DEFAULT 0, is_read INTEGER DEFAULT 0, cache_roomnames TEXT, item_type INTEGER DEFAULT 0,
  associated_message_guid TEXT, associated_message_type INTEGER DEFAULT 0, attributedBody BLOB,
  cache_has_attachments INTEGER DEFAULT 0, thread_originator_guid TEXT);
CREATE TABLE chat_handle_join (chat_id INTEGER REFERENCES chat (ROWID) ON DELETE CASCADE,
  handle_id INTEGER REFERENCES handle (ROWID) ON DELETE CASCADE, UNIQUE(chat_id, handle_id));
CREATE TABLE chat_message_join (chat_id INTEGER REFERENCES chat (ROWID) ON DELETE CASCADE,
  message_id INTEGER REFERENCES message (ROWID) ON DELETE CASCADE, message_date INTEGER DEFAULT 0,
  PRIMARY KEY (chat_id, message_id));
"""

HANDLES = {  # key: (id, service)
    "alice": ("+14155550101", "iMessage"),
    "bob": ("+14155550102", "iMessage"),
    "carol": ("carol@personal.example", "iMessage"),
    "stranger": ("+14155550177", "SMS"),
    "shortcode": ("72975", "SMS"),
    "spam": ("+14155550188", "SMS"),
}

# chat key: (style, display_name, members)   style 45 = one-to-one, 43 = group
CHATS = {
    "dm_alice": (45, None, ["alice"]),
    "dm_bob": (45, None, ["bob"]),
    "dm_carol": (45, None, ["carol"]),
    "dm_stranger": (45, None, ["stranger"]),
    "dm_shortcode": (45, None, ["shortcode"]),
    "dm_spam": (45, None, ["spam"]),
    "ski": (43, "Ski Trip", ["alice", "bob", "carol"]),
    "unnamed": (43, None, ["alice", "bob"]),
}

# (chat, from handle key or "me", days from now, text, extra)
MESSAGES = [
    ("dm_bob", "bob", -420, "Long time no see, Bob here", {}),
    ("dm_bob", "me", -419, "Great to hear from you", {}),
    ("dm_alice", "alice", -5, "Are we still on for the ski trip?", {}),
    ("dm_alice", "me", -4, "Yes! Booked the cabin", {}),
    ("dm_alice", "alice", -4, None, {"associated_message_type": 2000}),  # a tapback, not a message
    ("dm_alice", "alice", -3, None, {"attributedBody": "Typedstream hello from Alice"}),
    ("dm_alice", "me", -3, None, {"item_type": 1}),  # system row
    ("dm_carol", "carol", -10, "Dinner next week?", {}),
    ("dm_carol", "me", -10, "Sounds good", {}),
    ("dm_stranger", "stranger", -30, "Hi it's Pat from the conference", {}),
    ("dm_stranger", "me", -30, "Hey Pat!", {}),
    ("dm_shortcode", "shortcode", -8, "Your code is 123456", {}),
    ("dm_shortcode", "me", -8, "STOP", {}),
    ("dm_spam", "spam", -6, "You won a prize", {}),
    ("ski", "alice", -2, "Ski trip packing list: helmets, goggles", {}),
    ("ski", "bob", -2, "I'll bring snacks", {}),
    ("ski", "me", -1, "See you all Friday", {}),
    ("unnamed", "bob", -12, "Lunch crew?", {}),
]


def build_messages(path):
    db = make(path, MESSAGES_SCHEMA)
    hid = {}
    for k, (ident, service) in HANDLES.items():
        hid[k] = db.execute("INSERT INTO handle (id, service, country) VALUES (?,?, 'us')",
                            (ident, service)).lastrowid
    cid = {}
    for k, (style, name, members) in CHATS.items():
        cid[k] = db.execute("INSERT INTO chat (guid, style, chat_identifier, display_name) VALUES (?,?,?,?)",
                            (f"iMessage;-;{k}", style, k, name)).lastrowid
        for m in members:
            db.execute("INSERT INTO chat_handle_join VALUES (?,?)", (cid[k], hid[m]))
    for i, (chat, who, days, text, extra) in enumerate(MESSAGES):
        mine = who == "me"
        handle = hid[CHATS[chat][2][0]] if mine and CHATS[chat][0] == 45 else (0 if mine else hid[who])
        body = extra.get("attributedBody")
        mid = db.execute("""INSERT INTO message (guid, text, handle_id, date, is_from_me, item_type,
            associated_message_type, attributedBody) VALUES (?,?,?,?,?,?,?,?)""",
            (f"MSG-{i}", text, handle, apple_ns(days), int(mine), extra.get("item_type", 0),
             extra.get("associated_message_type", 0), typedstream(body) if body else None)).lastrowid
        db.execute("INSERT INTO chat_message_join VALUES (?,?,?)", (cid[chat], mid, apple_ns(days)))
    db.commit()
    db.close()
    return path


# ---------- Calendar ----------

CALENDAR_SCHEMA = """
CREATE TABLE Store (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, type INTEGER, disabled INTEGER);
CREATE TABLE Calendar (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, store_id INTEGER, title TEXT);
CREATE TABLE Identity (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, display_name TEXT, address TEXT,
  first_name TEXT, last_name TEXT);
CREATE TABLE CalendarItem (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, summary TEXT, location_id INTEGER,
  description TEXT, start_date REAL, start_tz TEXT, end_date REAL, all_day INTEGER, calendar_id INTEGER,
  organizer_id INTEGER, status INTEGER, has_recurrences INTEGER);
CREATE TABLE Participant (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, entity_type INTEGER, type INTEGER,
  status INTEGER, role INTEGER, identity_id INTEGER, owner_id INTEGER, email TEXT, is_self INTEGER);
CREATE TABLE OccurrenceCache (day REAL, event_id INTEGER, calendar_id INTEGER, store_id INTEGER,
  occurrence_date REAL, occurrence_start_date REAL, occurrence_end_date REAL);
"""

ME = "me@self.example"
# (summary, start days, [(email or None, display name, status, is_self)], occurrence days)
EVENTS = [
    ("Coffee with Alice", -20, [(ME, "Me", 2, 1), ("alice@acme.example", "Alice Anders", 2, 0)], None),
    ("Project sync", 7, [(ME, "Me", 2, 1), ("alice@acme.example", "Alice Anders", 2, 0),
                         ("erin@initrode.example", "Erin Evans", 4, 0),
                         ("room-4@resource.calendar.google.com", "Room 4", 2, 0)], None),
    ("1:1 Erin", -21, [(ME, "Me", 2, 1), ("erin@initrode.example", "Erin Evans", 2, 0)], [-21, -14, 7]),
    ("Pitch review", -9, [(ME, "Me", 2, 1), ("bob@globex.example", "Bob Baker", 3, 0)], None),
    ("All hands", -3, [(ME, "Me", 2, 1)] + [(f"staff{i}@acme.example", f"Staff {i}", 2, 0) for i in range(12)]
     + [("alice@acme.example", "Alice Anders", 2, 0)], None),
    ("Dentist", -1, [], None),  # nobody else: ignored
]


def build_calendar(path):
    db = make(path, CALENDAR_SCHEMA)
    db.execute("INSERT INTO Store (name, type) VALUES (?, 2)", (ME,))
    db.execute("INSERT INTO Store (name, type) VALUES ('On My Mac', 0)")
    db.execute("INSERT INTO Calendar (store_id, title) VALUES (1, 'Work')")
    for summary, start, people, occurrences in EVENTS:
        eid = db.execute("INSERT INTO CalendarItem (summary, start_date, end_date, calendar_id, has_recurrences) "
                         "VALUES (?,?,?,1,?)", (summary, apple_s(start), apple_s(start) + 1800,
                                                int(bool(occurrences)))).lastrowid
        for email, name, status, is_self in people:
            ident = db.execute("INSERT INTO Identity (display_name, address) VALUES (?,?)",
                               (name, f"mailto:{email}")).lastrowid
            db.execute("INSERT INTO Participant (entity_type, status, identity_id, owner_id, email, is_self) "
                       "VALUES (7,?,?,?,?,?)", (status, ident, eid, email, is_self))
        for d in occurrences or []:
            db.execute("INSERT INTO OccurrenceCache (day, event_id, calendar_id, store_id, occurrence_date, "
                       "occurrence_start_date, occurrence_end_date) VALUES (?,?,1,1,?,?,?)",
                       (apple_s(d), eid, apple_s(d), apple_s(d), apple_s(d) + 1800))
    db.commit()
    db.close()
    return path


# ---------- Mail ----------

MAIL_SCHEMA = """
CREATE TABLE mailboxes (ROWID INTEGER PRIMARY KEY, url UNIQUE, total_count INTEGER DEFAULT 0,
  unread_count INTEGER DEFAULT 0);
CREATE TABLE addresses (ROWID INTEGER PRIMARY KEY, address COLLATE NOCASE, comment, UNIQUE(address, comment));
CREATE TABLE subjects (ROWID INTEGER PRIMARY KEY, subject COLLATE RTRIM, normalized_subject COLLATE RTRIM);
CREATE TABLE summaries (ROWID INTEGER PRIMARY KEY, summary UNIQUE);
CREATE TABLE messages (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, message_id, global_message_id,
  remote_id INTEGER, document_id TEXT, sender, subject_prefix, subject, summary, date_sent INTEGER,
  date_received INTEGER, mailbox INTEGER, remote_mailbox INTEGER, flags INTEGER, read, flagged,
  deleted INTEGER DEFAULT 0, size, conversation_id INTEGER, list_id_hash INTEGER,
  unsubscribe_type INTEGER, automated_conversation INTEGER DEFAULT 0);
CREATE TABLE recipients (ROWID INTEGER PRIMARY KEY, message INTEGER, address INTEGER, type INTEGER,
  position INTEGER);
"""

ACCOUNT = "imap://8D2F7C3A-FAKE-ACCOUNT"
MAILBOXES = ["INBOX", "Sent Messages", "[Gmail]/All Mail", "Junk", "Trash", "Drafts"]
MAIL_ADDRESSES = {
    "me": (ME, "Me Myself"),
    "alice": ("alice@acme.example", "Alice Anders"),
    "hank": ("hank@hooli.example", "Hank Hill"),
    "noreply": ("noreply@service.example", "Service"),
    "list": ("digest@lists.example", "Weekly Digest"),
    "spam": ("winner@junk.example", "Prize Desk"),
    "inbound": ("ina@inbound.example", "Ina Bound"),
    "solo": ("mononym@single.example", "Madonna"),
}
# (sender, [recipients], mailbox, days, subject, summary, extra)
MAILS = [
    ("alice", ["me"], "INBOX", -6, "Q3 plan", "Draft of the Q3 plan attached", {"gid": 1001, "emlx": True}),
    ("alice", ["me"], "[Gmail]/All Mail", -6, "Q3 plan", "Draft of the Q3 plan attached", {"gid": 1001}),
    ("me", ["alice"], "Sent Messages", -5, "Q3 plan", "Looks great, two comments", {"gid": 1002, "prefix": "Re: "}),
    ("hank", ["me"], "INBOX", -40, "Propane pricing", "Following up on our chat", {"gid": 1003}),
    ("me", ["hank", "alice"], "Sent Messages", -39, "Propane pricing", "Thanks Hank", {"gid": 1004, "prefix": "Re: "}),
    ("noreply", ["me"], "INBOX", -2, "Your receipt", "Thanks for your order", {"gid": 1005}),
    ("list", ["me"], "INBOX", -1, "This week", "Top stories", {"gid": 1006, "list_id_hash": 99}),
    ("spam", ["me"], "Junk", -1, "You won", "Claim now", {"gid": 1007}),
    ("inbound", ["me"], "INBOX", -15, "Intro", "Saw your talk", {"gid": 1008}),
    ("solo", ["me"], "INBOX", -16, "Hello", "One name only", {"gid": 1009}),
    ("me", ["solo"], "Sent Messages", -16, "Hello", "Hi", {"gid": 1010, "prefix": "Re: "}),
    ("me", ["inbound"], "Drafts", -14, "Unsent draft", "Not sent", {"gid": 1011}),
]

EMLX_BODY = """From: Alice Anders <alice@acme.example>
To: Me Myself <me@self.example>
Subject: Q3 plan
Message-ID: <q3-plan@acme.example>
Content-Type: text/plain; charset=utf-8

Here is the Q3 plan we discussed.
Main risk is the hiring timeline.

On Tue, Me Myself wrote:
> quoted text that should not appear
"""


def build_mail(root):
    """Mail/V10 (current) and a stale Mail/V9 left behind by an older macOS."""
    stale = make(os.path.join(root, "V9", "MailData", "Envelope Index"), MAIL_SCHEMA)
    stale.execute("INSERT INTO mailboxes (url) VALUES (?)", (f"{ACCOUNT}/INBOX",))
    stale.execute("INSERT INTO addresses (address, comment) VALUES ('stale@old.example', 'Stale Person')")
    stale.execute("INSERT INTO messages (sender, mailbox, date_received, date_sent) VALUES (1, 1, ?, ?)",
                  (unix(-900), unix(-900)))
    stale.commit()
    stale.close()

    v10 = os.path.join(root, "V10")
    db = make(os.path.join(v10, "MailData", "Envelope Index"), MAIL_SCHEMA)
    mbox = {name: db.execute("INSERT INTO mailboxes (url) VALUES (?)",
                             (f"{ACCOUNT}/{name.replace(' ', '%20')}",)).lastrowid for name in MAILBOXES}
    addr = {k: db.execute("INSERT INTO addresses (address, comment) VALUES (?,?)", v).lastrowid
            for k, v in MAIL_ADDRESSES.items()}
    for sender, rcpts, box, days, subject, summary, extra in MAILS:
        sid = db.execute("INSERT INTO subjects (subject) VALUES (?)", (subject,)).lastrowid
        sm = db.execute("INSERT OR IGNORE INTO summaries (summary) VALUES (?)", (summary,)).lastrowid
        mid = db.execute("""INSERT INTO messages (message_id, global_message_id, sender, subject_prefix, subject,
            summary, date_sent, date_received, mailbox, list_id_hash) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (extra["gid"], extra["gid"], addr[sender], extra.get("prefix"), sid, sm, unix(days) - 60,
             unix(days), mbox[box], extra.get("list_id_hash"))).lastrowid
        for pos, r in enumerate(rcpts):
            db.execute("INSERT INTO recipients (message, address, type, position) VALUES (?,?,0,?)",
                       (mid, addr[r], pos))
        if extra.get("emlx"):
            d = os.path.join(v10, "8D2F7C3A-FAKE-ACCOUNT", "INBOX.mbox", "C0FFEE00-FAKE", "Data", "1",
                             "Messages")
            os.makedirs(d, exist_ok=True)
            raw = EMLX_BODY.encode("utf-8")
            with open(os.path.join(d, f"{mid}.emlx"), "wb") as f:
                f.write(f"{len(raw)}\n".encode() + raw + b'<?xml version="1.0"?>\n<plist version="1.0"><dict/></plist>\n')
    db.commit()
    db.close()
    return os.path.join(root, "V*", "MailData", "Envelope Index")


# ---------- LinkedIn ----------

LINKEDIN_CSV = """Notes:
"When exporting your connection data, you may notice that some of the email addresses are missing."

First Name,Last Name,URL,Email Address,Company,Position,Connected On
Bob,Baker,https://www.linkedin.com/in/bob-baker-fake/,,Globex,Director of Product,12 Mar 2021
Frank,Foster,https://www.linkedin.com/in/frank-foster-fake/,frank@stark.example,Stark Industries,Engineer,01 Jan 2024
Erin,Evans,https://www.linkedin.com/in/erin-evans-fake/,erin@initrode.example,Initrode,Chief Revenue Officer,05 May 2022
,,,,,,
"""


def build_linkedin(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write(LINKEDIN_CSV)
    return path


def build(dest):
    """Write every fixture under dest and return the CRM_* environment pointing at them."""
    return {
        "CRM_CONTACTS_GLOB": build_contacts(os.path.join(dest, "AddressBook")),
        "CRM_MESSAGES_DB": build_messages(os.path.join(dest, "Messages", "chat.db")),
        "CRM_CALENDAR_DB": build_calendar(os.path.join(dest, "Calendars", "Calendar.sqlitedb")),
        "CRM_MAIL_GLOB": build_mail(os.path.join(dest, "Mail")),
        "LINKEDIN_CSV": build_linkedin(os.path.join(dest, "Downloads", "Connections.csv")),
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    for k, v in build(os.path.abspath(sys.argv[1])).items():
        print(f"export {k}='{v}'")
