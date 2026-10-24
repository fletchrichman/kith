"""status and doctor: source access, sync health and setup gaps."""
import glob, os, platform, sqlite3, sys

from kith.config import CALENDAR_DB, CONTACTS_GLOB, DB_PATH, MAIL_GLOB, MESSAGES_DB, SETUP_URL
from kith.sources import SYNCERS
from kith.sources.base import FormatChanged, macos_version, open_ro, open_source
from kith.sources.mail import mail_index
from kith.util import apple_ts, days_ago


def cmd_status(db, a):
    for r in db.execute("SELECT * FROM sync_log ORDER BY source"):
        if r["last_success_at"]:
            print(f"{r['source']}: last success {r['last_success_at']} UTC - {r['detail']}")
        else:
            print(f"{r['source']}: never synced successfully")
        if r["last_error"]:
            print(f"  last attempt FAILED {r['last_error_at']} UTC - {r['last_error']}")
    c = db.execute("""SELECT (SELECT count(*) FROM v_people), (SELECT count(*) FROM identities WHERE person_id IS NULL),
        (SELECT min(first_at) FROM msg_stats), (SELECT max(max(coalesce(last_in_at,'')), max(coalesce(last_out_at,''))) FROM msg_stats)""").fetchone()
    print(f"{c[0]} people, {c[1]} unlinked identities. Messages history on this Mac spans {c[2] or '?'} to {c[3] or '?'}.")
    print("Note: Messages only covers what's stored on this Mac; with Messages in iCloud older history may be missing.")
    if db.execute("SELECT 1 FROM sync_log WHERE last_error IS NOT NULL").fetchone():
        print("Some sources are failing. Run `python3 crm.py doctor` for what to fix.")


SETUP_SECTIONS = {
    "fda": ("1. Full Disk Access", "1-full-disk-access-required"),
    "contacts": ("2. Contacts", "2-contacts"),
    "messages": ("3. Messages", "3-messages-imessage-and-sms"),
    "calendar": ("4. Calendar", "4-calendar"),
    "mail": ("5. Mail", "5-mail"),
    "linkedin": ("6. LinkedIn", "6-linkedin-optional"),
    "troubleshooting": ("Troubleshooting", "troubleshooting"),
}


APPS = {"com.anthropic.claudefordesktop": "Claude", "com.apple.Terminal": "Terminal",
        "com.googlecode.iterm2": "iTerm", "com.mitchellh.ghostty": "Ghostty", "dev.warp.Warp-Stable": "Warp",
        "com.microsoft.VSCode": "Visual Studio Code", "com.todesktop.230313mzl4w4u92": "Cursor"}


def blocked(path):
    """True if macOS privacy protection stops us listing the folder that should hold `path`
    (without Full Disk Access, protected files look like they don't exist)."""
    d = os.path.dirname(path.split("*")[0])
    while d and d != "/" and not os.path.isdir(d):
        d = os.path.dirname(d)
    try:
        os.listdir(d)
        return False
    except PermissionError:
        return True


def check_source(name):
    """(state, detail) for reading one source right now: ok, missing, denied, format or error."""
    if name == "contacts":
        paths = sorted(set(glob.glob(CONTACTS_GLOB, recursive=True)))
        pattern = CONTACTS_GLOB
    elif name == "mail":
        paths = glob.glob(MAIL_GLOB) and [mail_index()]
        pattern = MAIL_GLOB
    else:
        pattern = MESSAGES_DB if name == "messages" else CALENDAR_DB
        paths = [pattern] if os.path.exists(pattern) else []
    if not paths:
        return ("denied", f"can't see {pattern}") if blocked(pattern) else ("missing", f"nothing at {pattern}")
    for path in paths:
        try:
            open_source(path, name.capitalize()).close()
        except PermissionError:
            return "denied", f"can't open {path}"
        except FormatChanged as e:
            return "format", str(e)
        except sqlite3.Error as e:
            return "error", f"{path}: {e}"
    return "ok", paths[0] if len(paths) == 1 else f"{len(paths)} address books"


def cmd_doctor(db, a):
    """Check each source's access, format and sync history, pointing at the SETUP.md fix."""
    problems = []
    def ok(msg):
        print(f"  ok    {msg}")
    def warn(msg, section):
        title, anchor = SETUP_SECTIONS[section]
        problems.append(msg)
        print(f"  WARN  {msg}\n        fix: SETUP.md \"{title}\" {SETUP_URL}#{anchor}")
    print(f"kith doctor (macOS {macos_version()}, Python {platform.python_version()})")

    states, denied = {}, []
    for name in SYNCERS:
        print(f"{name}:")
        state, detail = states[name] = check_source(name)
        if state == "ok":
            ok(f"readable ({detail})")
        elif state == "denied":
            denied.append(name)
            warn(f"no access: {detail}. This app needs Full Disk Access.", "fda")
        elif state == "missing":
            warn(f"not set up: {detail}", name)
        elif state == "format":
            warn(detail, "troubleshooting")
        else:
            warn(f"can't read: {detail}", "troubleshooting")
        r = db.execute("SELECT * FROM sync_log WHERE source=?", (name,)).fetchone()
        if r is None:
            if state == "ok":
                warn("never synced. Run: python3 crm.py sync " + name, name)
        else:
            if r["last_success_at"]:
                ok(f"last synced {r['last_success_at']} UTC: {r['detail']}")
            if r["last_error"] and state == "ok":  # if it's failing right now, that was reported above
                section = "fda" if "Full Disk Access" in r["last_error"] else \
                          "troubleshooting" if "changed the format" in r["last_error"] else name
                warn(f"last sync FAILED {r['last_error_at']} UTC: {r['last_error']}", section)

        if name == "messages" and state == "ok":
            src = open_ro(MESSAGES_DB)
            first, last = src.execute("SELECT min(date), max(date) FROM message WHERE date > 0").fetchone()
            src.close()
            span = days_ago(apple_ts(first))
            if span is None:
                warn("no messages on this Mac. Turn on Messages in iCloud.", "messages")
            elif span < 330:
                warn(f"history only goes back to {apple_ts(first)[:10]} ({span} days). Messages in iCloud "
                     "may be off or still downloading.", "messages")
            else:
                ok(f"history spans {apple_ts(first)[:10]} to {apple_ts(last)[:10]}")
            if span is not None and days_ago(apple_ts(last)) > 14:
                warn(f"newest message is from {apple_ts(last)[:10]}; Messages on this Mac may have stopped syncing.",
                     "messages")
        if name == "calendar" and r and r["last_success_at"] and \
                not db.execute("SELECT 1 FROM events LIMIT 1").fetchone():
            warn("0 events with other attendees. Is your work calendar added to Calendar.app?", "calendar")
        if name == "mail" and state == "ok":
            mine = [x[0] for x in db.execute("SELECT address FROM self_addresses ORDER BY address")]
            if mine:
                more = f" and {len(mine) - 4} more" if len(mine) > 4 else ""
                ok(f"your addresses: {', '.join(mine[:4])}{more} (list: python3 crm.py me; "
                   "add aliases: python3 crm.py me --add you@x.com)")
            else:
                warn("no addresses of yours known, so sent and received mail can't be told apart. "
                     "Add them with: python3 crm.py me --add you@x.com", "mail")
            n = db.execute("SELECT count(*), coalesce(sum(n_out), 0) FROM email_stats").fetchone()
            if n[0] and not n[1]:
                warn(f"{n[0]} people have emailed you but no sent mail was found. Gmail: show Sent Mail and "
                     "All Mail in IMAP; also check every address of yours is in `me`.", "mail")
            elif n[0]:
                ok(f"{n[0]} human correspondents, {n[1]} sent emails counted")

    if denied:
        bundle = os.environ.get("__CFBundleIdentifier", "")
        app = APPS.get(bundle) or bundle or os.environ.get("TERM_PROGRAM") or "the app running this"
        print(f"\n{', '.join(denied)} blocked: give {app} Full Disk Access, then quit and reopen it.")
    r = db.execute("SELECT last_success_at FROM sync_log WHERE source='linkedin'").fetchone()
    print("linkedin (optional):")
    if r and r[0]:
        ok(f"last imported {r[0][:10]}")
    else:
        print(f"  --    not imported; see SETUP.md \"6. LinkedIn\" {SETUP_URL}#6-linkedin-optional")
    print("crm.db:")
    mode = os.stat(DB_PATH).st_mode & 0o777
    if mode & 0o077:
        warn(f"{DB_PATH} is readable by other users (mode {mode:o}). Run: chmod 600 \"{DB_PATH}\"",
             "troubleshooting")
    else:
        ok(f"{DB_PATH} (owner-only)")
    print(f"\n{len(problems)} problem(s) found." if problems else "\nAll good.")
    if problems:
        sys.exit(1)
