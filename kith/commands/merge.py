"""dupes, merge and unmerge."""
import json, sys

from kith.db import one_person


def merge_people(db, keep_id, drop_id, reason):
    keep = db.execute("SELECT * FROM people WHERE id=?", (keep_id,)).fetchone()
    drop = db.execute("SELECT * FROM people WHERE id=?", (drop_id,)).fetchone()
    moved = {
        "identities": [[r[0], r[1]] for r in db.execute("SELECT kind, value FROM identities WHERE person_id=?", (drop_id,))],
        "notes": [r[0] for r in db.execute("SELECT id FROM notes WHERE person_id=?", (drop_id,))],
        "source_keys": [[r[0], r[1]] for r in db.execute("SELECT source, key FROM source_keys WHERE person_id=?", (drop_id,))],
        "affiliations": [[r[0], r[1]] for r in db.execute("SELECT org, source FROM affiliations WHERE person_id=?", (drop_id,))],
        "fields": {k: drop[k] for k in ("relationship", "cadence_days", "company", "title")},
        "keep_fields": {k: keep[k] for k in ("relationship", "cadence_days", "company", "title")},
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
    # Fields the merge filled in on the kept person from the dropped one go back to empty,
    # unless you've changed them since (merges logged before keep_fields existed are left alone).
    for k, before in moved.get("keep_fields", {}).items():
        if before is None:
            db.execute(f"UPDATE people SET {k}=NULL WHERE id=? AND {k} IS ?", (m["keep_id"], moved["fields"][k]))
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
