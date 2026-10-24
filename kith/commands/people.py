"""Read commands over crm.db: person, search, at, catchup, recent, top, unknown."""
import re
from datetime import datetime, timezone

from kith.db import find_people, one_person
from kith.util import ago, days_ago


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
