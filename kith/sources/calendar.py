"""Calendar sync: meetings with other attendees."""
from kith.config import CALENDAR_DB
from kith.db import TABLES, log_sync
from kith.sources.base import open_source
from kith.util import apple_ts, norm_email


def sync_calendar(db):
    src = open_source(CALENDAR_DB, "Calendar")
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
    db.executescript(TABLES)
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
