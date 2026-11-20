"""Messages (chat.db) sync. Message text is never copied into crm.db."""
from kith.config import MESSAGES_DB
from kith.db import log_sync
from kith.sources.base import open_source
from kith.util import apple_ts, norm_handle


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
    src = open_source(MESSAGES_DB, "Messages")
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
        SELECT c.ROWID AS id, c.display_name AS name,
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
