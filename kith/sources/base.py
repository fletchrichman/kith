"""Opening Apple databases read-only and checking their format before reading."""
import os, platform, sqlite3

from kith.config import ISSUES_URL


def open_ro(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"not found: {path}")
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
    except sqlite3.DatabaseError as e:
        raise PermissionError(f"{path}: {e}. Grant Full Disk Access to the app running this "
                              "(System Settings > Privacy & Security > Full Disk Access).")
    return db


class FormatChanged(RuntimeError):
    pass


def macos_version():
    return platform.mac_ver()[0] or "unknown"


# Columns each reader uses, checked before querying so a macOS update that renames something
# fails with a clear message instead of a raw "no such column". (ROWID is implicit, not listed.)
REQUIRED = {
    "Contacts": {
        "ZABCDRECORD": "Z_PK ZUNIQUEID ZFIRSTNAME ZLASTNAME ZORGANIZATION ZJOBTITLE",
        "ZABCDPHONENUMBER": "ZOWNER ZFULLNUMBER",
        "ZABCDEMAILADDRESS": "ZOWNER ZADDRESS",
    },
    "Messages": {
        "message": "date is_from_me associated_message_type item_type handle_id text attributedBody",
        "chat": "style display_name",
        "handle": "id",
        "chat_handle_join": "chat_id handle_id",
        "chat_message_join": "chat_id message_id",
    },
    "Calendar": {
        "Participant": "owner_id email identity_id is_self status",
        "Identity": "address display_name",
        "Store": "name",
        "CalendarItem": "summary start_date",
        "OccurrenceCache": "event_id occurrence_start_date",
    },
    "Mail": {
        "messages": "sender mailbox subject subject_prefix summary date_received date_sent "
                    "global_message_id list_id_hash automated_conversation deleted",
        "addresses": "address comment",
        "mailboxes": "url",
        "recipients": "message address",
        "subjects": "subject",
        "summaries": "summary",
    },
}


def check_format(src, source):
    """Raise FormatChanged if a table or column this source's reader needs is gone."""
    problems = []
    for table, cols in REQUIRED[source].items():
        have = {r[1] for r in src.execute(f"PRAGMA table_info({table})")}
        if not have:
            problems.append(f"table {table} is missing")
            continue
        missing = [c for c in cols.split() if c not in have]
        if missing:
            problems.append(f"{table} has no {', '.join(missing)}")
    if problems:
        raise FormatChanged(f"Apple changed the format of {source} on macOS {macos_version()} "
                            f"({'; '.join(problems)}). The other sources still work; please report "
                            f"this at {ISSUES_URL}")


def open_source(path, source):
    src = open_ro(path)
    check_format(src, source)
    return src
