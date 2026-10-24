"""LinkedIn Connections.csv import."""
import csv, os
from datetime import datetime

from kith.db import link_identity, live, log_sync
from kith.util import norm_email, norm_name


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
