"""Command-line parsing: python3 crm.py <command> [args]."""
import argparse

from kith.commands.doctor import cmd_doctor, cmd_status
from kith.commands.live import cmd_emails, cmd_grep, cmd_messages
from kith.commands.merge import cmd_dupes, cmd_merge, cmd_unmerge
from kith.commands.people import cmd_at, cmd_catchup, cmd_person, cmd_recent, cmd_search, cmd_top, cmd_unknown
from kith.commands.sql import cmd_sql
from kith.commands.sync import auto_sync, cmd_sync
from kith.commands.writes import cmd_add, cmd_link, cmd_me, cmd_note, cmd_set
from kith.db import connect


def main():
    ap = argparse.ArgumentParser(description="kith: your personal CRM")
    sp = ap.add_subparsers(dest="cmd", required=True)

    def command(name, f, help, *positional, limit=None):
        s = sp.add_parser(name, help=help)
        for arg in positional:
            s.add_argument(arg)
        if limit:
            s.add_argument("--limit", type=int, default=limit)
        s.set_defaults(f=f)
        return s

    s = command("sync", cmd_sync, "import from Contacts, Messages, Calendar (+ --linkedin CSV)")
    # No argparse choices: Python < 3.12 rejects an empty nargs="*" list against them; cmd_sync validates.
    s.add_argument("sources", nargs="*", metavar="{contacts,messages,calendar,mail}")
    s.add_argument("--linkedin", help="path to LinkedIn Connections.csv")
    command("person", cmd_person, "everything about one person", "query")
    command("search", cmd_search, "find people by name/phone/email", "query")
    command("at", cmd_at, "who do I know at an organization", "org")
    s = command("catchup", cmd_catchup, "labeled people you haven't been in touch with")
    s.add_argument("--label"); s.add_argument("--default-days", type=int, default=60); s.add_argument("--limit", type=int, default=25)
    command("recent", cmd_recent, "people you've messaged recently", limit=30)
    command("top", cmd_top, "people you message most (all time)", limit=50)
    command("unknown", cmd_unknown, "frequent two-way handles not in your contacts", limit=30)
    s = command("messages", cmd_messages, "read recent message text with a person (live from Messages)", "query", limit=40)
    s.add_argument("--grep"); s.add_argument("--groups", action="store_true", help="include their messages in group chats")
    command("grep", cmd_grep, "who did I talk to about X (searches all messages live)", "text", limit=30)
    s = command("emails", cmd_emails, "recent email with a person (subjects; --bodies for opening lines), live from Mail",
                "query", limit=25)
    s.add_argument("--grep"); s.add_argument("--bodies", action="store_true")
    command("note", cmd_note, "add a note to a person", "query", "text")
    s = command("set", cmd_set, "set relationship/cadence/snooze/name/company/title", "query")
    s.add_argument("--relationship"); s.add_argument("--cadence", type=int)
    s.add_argument("--snooze", help="YYYY-MM-DD"); s.add_argument("--name"); s.add_argument("--company"); s.add_argument("--title")
    s = command("add", cmd_add, "add a person manually", "name")
    s.add_argument("--handle", action="append"); s.add_argument("--company"); s.add_argument("--title"); s.add_argument("--relationship")
    command("link", cmd_link, "attach a phone/email/linkedin url to a person", "handle", "query")
    command("merge", cmd_merge, "merge DROP into KEEP", "keep", "drop")
    s = command("dupes", cmd_dupes, "same-name people to review (--auto merges the safe ones)", limit=50)
    s.add_argument("--auto", action="store_true")
    command("unmerge", cmd_unmerge, "undo a merge").add_argument("id", type=int)
    command("me", cmd_me, "list/add my own email addresses").add_argument("--add", action="append")
    command("status", cmd_status, "source freshness and coverage")
    command("doctor", cmd_doctor, "check access, sync health and setup for each source")
    command("sql", cmd_sql, "read-only SQL against crm.db", "query", limit=200)
    a = ap.parse_args()
    db = connect()
    if a.cmd not in ("sync", "sql", "doctor"):
        auto_sync(db)
    a.f(db, a)
