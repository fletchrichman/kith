"""Apple Contacts (AddressBook) sync."""
import glob

from kith.config import CONTACTS_GLOB
from kith.db import link_identity, live, log_sync
from kith.sources.base import open_source
from kith.util import norm_email, norm_name, norm_phone


def sync_contacts(db):
    files = sorted(set(glob.glob(CONTACTS_GLOB, recursive=True)))
    if not files:
        raise FileNotFoundError(f"No AddressBook databases at {CONTACTS_GLOB} (or no Full Disk Access).")
    seen = 0
    for f in files:
        src = open_source(f, "Contacts")
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
