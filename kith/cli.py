"""Command-line parsing: python3 crm.py <command> [args]."""
import argparse

from kith.commands.doctor import cmd_doctor, cmd_status
from kith.commands.live import cmd_emails, cmd_grep, cmd_messages
from kith.commands.merge import cmd_dupes, cmd_merge, cmd_unmerge
from kith.commands.people import cmd_at, cmd_catchup, cmd_person, cmd_recent, cmd_search, cmd_top, cmd_unknown
from kith.commands.sql import cmd_sql
from kith.commands.sync import auto_sync, cmd_sync
from kith.commands.todos import cmd_todo
from kith.commands.writes import cmd_add, cmd_link, cmd_me, cmd_note, cmd_set
from kith.db import connect


def main():
    ap = argparse.ArgumentParser(description="kith: your personal CRM")
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("sync", help="import from Contacts, Messages, Calendar (+ --linkedin CSV)")
    # No argparse choices: Python < 3.12 rejects an empty nargs="*" list against them; cmd_sync validates.
    s.add_argument("sources", nargs="*", metavar="{contacts,messages,calendar,mail}")
    s.add_argument("--linkedin", help="path to LinkedIn Connections.csv")
    s.set_defaults(f=cmd_sync)
    s = sp.add_parser("person", help="everything about one person"); s.add_argument("query"); s.set_defaults(f=cmd_person)
    s = sp.add_parser("search", help="find people by name/phone/email"); s.add_argument("query"); s.set_defaults(f=cmd_search)
    s = sp.add_parser("at", help="who do I know at an organization"); s.add_argument("org"); s.set_defaults(f=cmd_at)
    s = sp.add_parser("catchup", help="labeled people you haven't been in touch with")
    s.add_argument("--label"); s.add_argument("--default-days", type=int, default=60); s.add_argument("--limit", type=int, default=25)
    s.set_defaults(f=cmd_catchup)
    s = sp.add_parser("recent", help="people you've messaged recently"); s.add_argument("--limit", type=int, default=30); s.set_defaults(f=cmd_recent)
    s = sp.add_parser("top", help="people you message most (all time)"); s.add_argument("--limit", type=int, default=50); s.set_defaults(f=cmd_top)
    s = sp.add_parser("unknown", help="frequent two-way handles not in your contacts"); s.add_argument("--limit", type=int, default=30); s.set_defaults(f=cmd_unknown)
    s = sp.add_parser("messages", help="read recent message text with a person (live from Messages)")
    s.add_argument("query"); s.add_argument("--limit", type=int, default=40); s.add_argument("--grep")
    s.add_argument("--groups", action="store_true", help="include their messages in group chats")
    s.set_defaults(f=cmd_messages)
    s = sp.add_parser("grep", help="who did I talk to about X (searches all messages live)")
    s.add_argument("text"); s.add_argument("--limit", type=int, default=30); s.set_defaults(f=cmd_grep)
    s = sp.add_parser("emails", help="recent email with a person (subjects; --bodies for opening lines), live from Mail")
    s.add_argument("query"); s.add_argument("--limit", type=int, default=25); s.add_argument("--grep")
    s.add_argument("--bodies", action="store_true"); s.set_defaults(f=cmd_emails)
    s = sp.add_parser("note", help="add a note to a person"); s.add_argument("query"); s.add_argument("text"); s.set_defaults(f=cmd_note)
    s = sp.add_parser("todo", help="to-do list: open items, overdue first (add/done/undo/rm/edit)")
    s.add_argument("--all", action="store_true", help="include done items"); s.add_argument("--done", action="store_true", help="only done items")
    s.add_argument("--person", dest="list_person", metavar="PERSON", help="only items about this person"); s.set_defaults(f=cmd_todo)
    ts = s.add_subparsers(dest="action")
    due_help = "YYYY-MM-DD, today, tomorrow, a weekday, +3d or +2w"
    t = ts.add_parser("add", help="add an item"); t.add_argument("text"); t.add_argument("--person"); t.add_argument("--due", help=due_help)
    t.add_argument("--linear", action="store_const", const=True, help="also create a Linear issue (after todo linear setup)")
    t.add_argument("--no-linear", dest="linear", action="store_const", const=False, help="don't, even with --push-all on")
    t.add_argument("--share-person", action="store_true", help="put the person's name in the Linear issue (off by default)")
    for name, what in (("done", "mark done"), ("undo", "mark not done"), ("rm", "delete")):
        ts.add_parser(name, help=what).add_argument("id", type=int)
    t = ts.add_parser("edit", help="change text, due date or person ('none' clears)"); t.add_argument("id", type=int)
    t.add_argument("--text"); t.add_argument("--due", help=due_help + ", or none"); t.add_argument("--person", help="a person, or none")
    t = ts.add_parser("sync", help="sync linked todos with Linear (newer change wins)")
    t.add_argument("--import-assigned", action="store_true", help="also import open issues assigned to you in the team")
    t = ts.add_parser("linear", help="opt-in Linear sync: setup or off").add_subparsers(dest="linear_action", required=True)
    u = t.add_parser("setup", help="connect a Linear team (key from LINEAR_API_KEY or the Keychain)")
    u.add_argument("--team", required=True, help="team key, e.g. ENG"); u.add_argument("--project", help="project name for new issues")
    u.add_argument("--push-all", action=argparse.BooleanOptionalAction, help="push every new todo to Linear")
    t.add_parser("off", help="stop syncing (links are kept)")
    s = sp.add_parser("set", help="set relationship/cadence/snooze/name/company/title")
    s.add_argument("query"); s.add_argument("--relationship"); s.add_argument("--cadence", type=int)
    s.add_argument("--snooze", help="YYYY-MM-DD"); s.add_argument("--name"); s.add_argument("--company"); s.add_argument("--title")
    s.set_defaults(f=cmd_set)
    s = sp.add_parser("add", help="add a person manually"); s.add_argument("name"); s.add_argument("--handle", action="append")
    s.add_argument("--company"); s.add_argument("--title"); s.add_argument("--relationship"); s.set_defaults(f=cmd_add)
    s = sp.add_parser("link", help="attach a phone/email/linkedin url to a person"); s.add_argument("handle"); s.add_argument("query"); s.set_defaults(f=cmd_link)
    s = sp.add_parser("merge", help="merge DROP into KEEP"); s.add_argument("keep"); s.add_argument("drop"); s.set_defaults(f=cmd_merge)
    s = sp.add_parser("dupes", help="same-name people to review (--auto merges the safe ones)")
    s.add_argument("--limit", type=int, default=50); s.add_argument("--auto", action="store_true"); s.set_defaults(f=cmd_dupes)
    s = sp.add_parser("unmerge", help="undo a merge"); s.add_argument("id", type=int); s.set_defaults(f=cmd_unmerge)
    s = sp.add_parser("me", help="list/add my own email addresses"); s.add_argument("--add", action="append"); s.set_defaults(f=cmd_me)
    s = sp.add_parser("status", help="source freshness and coverage"); s.set_defaults(f=cmd_status)
    s = sp.add_parser("doctor", help="check access, sync health and setup for each source"); s.set_defaults(f=cmd_doctor)
    s = sp.add_parser("sql", help="read-only SQL against crm.db"); s.add_argument("query"); s.add_argument("--limit", type=int, default=200); s.set_defaults(f=cmd_sql)
    a = ap.parse_args()
    db = connect()
    if a.cmd not in ("sync", "sql", "doctor"):
        auto_sync(db)
    a.f(db, a)
