"""Your own edits: me, note, set, add, link. All survive re-syncs."""
from kith.db import one_person
from kith.util import norm_email, norm_handle


def cmd_me(db, a):
    """Show or add my own email addresses (used to tell sent from received mail)."""
    for e in a.add or []:
        db.execute("INSERT OR IGNORE INTO self_addresses VALUES (?, 'manual')", (norm_email(e),))
    db.commit()
    for r in db.execute("SELECT * FROM self_addresses ORDER BY address"):
        print(f"{r['address']} ({r['source']})")


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
