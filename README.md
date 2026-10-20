# kith

*Kith (n.): your friends, acquaintances and neighbors, as in "kith and kin."*

A local-first personal CRM for macOS that you query by talking to
[Claude Code](https://claude.com/claude-code). No account, no server, no sync service: one Python
file (standard library only) builds a SQLite database (`crm.db`) from data already on your Mac,
and Claude answers questions like:

- "Who do I know at Stripe?"
- "Prep me for my meeting with Jane tomorrow."
- "Who haven't I talked to in a while?"
- "Who did I talk to about the offsite?"
- "Remember that Sam just moved to Denver."

Everything stays on your machine (the database is `~/Library/Application Support/kith/crm.db`). Source data is opened read-only, and message and email
text are never copied into `crm.db`; they're read live from Messages and Mail when you ask.
It cannot send messages or emails.

## What it reads

| Source | What it uses |
| --- | --- |
| Apple Contacts | names, phones, emails, company and title |
| Messages (iMessage/SMS) | who you text, how often, when; text read live on request |
| Calendar | meetings with people (any account synced to the macOS Calendar app) |
| Apple Mail | who you email and when, from Mail's local index (any account added to Mail.app) |
| LinkedIn (optional) | your `Connections.csv` export: current company and title |

Gmail or Outlook only reach the CRM if those accounts are added to Apple Mail / Calendar.

## Requirements

- macOS, with Python 3.9+ (the built-in `python3` from Xcode Command Line Tools works;
  run `xcode-select --install` if it's missing). No packages to install.
- [Claude Code](https://claude.com/claude-code) (CLI, desktop app, or IDE extension), v2.1.275+ for the
  one-step plugin install.
- Full Disk Access for the app you run Claude Code from, so it can read Messages, Mail and
  Calendar data.

## Setup (about 15 minutes, plus background download time)

### Option A: install the Claude Code plugin (recommended)

1. In Claude Code, run:

   ```
   /plugin install kith --marketplace fletchrichman/kith
   ```

   On older Claude Code versions, add the marketplace first:
   `/plugin marketplace add fletchrichman/kith`, then `/plugin install kith@kith`.
2. Grant **Full Disk Access** to the app you run Claude Code in, then quit and reopen it
   ([SETUP.md step 1](SETUP.md#1-full-disk-access-required)).
3. Ask Claude: **"Run the kith doctor and help me fix what it finds."** It syncs each source and
   walks you through [SETUP.md](SETUP.md) for anything missing (Messages in iCloud, Apple Mail,
   your email addresses, the LinkedIn export).

Update with `/plugin` (Installed tab) or `claude plugin update kith@kith`. Your data isn't in the
plugin folder, so updating or reinstalling keeps it.

### Option B: clone the repo

Use this to hack on kith, or to run `python3 crm.py ...` yourself from a fixed folder.

1. Clone this repo somewhere permanent, for example:

   ```bash
   git clone https://github.com/fletchrichman/kith.git ~/Projects/kith
   ```
2. Run the installer:

   ```bash
   cd ~/Projects/kith && ./install.sh
   ```

   It checks Python, installs the `crm` skill into `~/.claude/skills/crm` (pointing at this
   folder), and runs the first sync. Don't also install the plugin; one copy of the skill is enough.
3. **Get each source syncing: follow [SETUP.md](SETUP.md).** It walks through Full Disk Access,
   then Contacts, Messages in iCloud, Calendar, Apple Mail (including Gmail settings) and the
   LinkedIn export, with a troubleshooting table. The first sync usually reports `FAILED` until
   Full Disk Access is granted. That's expected.
4. Check it: `python3 crm.py doctor` tests access to each source and flags setup gaps (short
   Messages history, missing email addresses, no sent mail), each with a link to the fix.

If you move the folder, run `./install.sh --no-sync` again so the skill points at the new path.

## Using it

Open Claude Code anywhere and ask about people; the skill tells Claude how to use the CRM.
Data refreshes automatically when it's more than 6 hours old.

You can also run commands yourself from a clone (Option B); `python3 crm.py -h` lists them all.
With the plugin, ask Claude to run them. Common ones:

```bash
python3 crm.py person "Jane Doe"
```

```bash
python3 crm.py at Stripe
```

```bash
python3 crm.py catchup
```

```bash
python3 crm.py note "Jane Doe" "Met at the Denver offsite; building an AI note-taker"
```

```bash
python3 crm.py dupes
```

See `CLAUDE.md` for the full command list and the rules Claude follows when answering.

## Privacy

- Your database is `~/Library/Application Support/kith/crm.db`, readable only by your user. A clone
  that already has a `crm.db` next to `crm.py` keeps using it (it's gitignored, like backups and CSVs);
  to share one database between a clone and the plugin, move it to that folder. Never commit or share
  it: it holds your contacts and your notes about people.
- Ask Claude to summarize rather than paste message history into other tools.
- When you ask Claude about people, the relevant CRM output (names, dates, notes, message
  excerpts you request) is sent to Anthropic as part of your Claude Code conversation, like any
  other file or command output Claude reads. The database itself never leaves your Mac.
- Uninstall: `claude plugin uninstall kith@kith` (or delete your clone and `~/.claude/skills/crm`),
  then delete `~/Library/Application Support/kith` to remove your data.

## Compatibility

- Tested on macOS 26 (Tahoe) with Python 3.9 (system) and 3.14. Older macOS versions likely work
  but haven't been tested.
- It reads Apple's private, undocumented database formats (Messages `chat.db`, the AddressBook
  and Calendar SQLite stores, Mail's `Envelope Index`). Apple changes these without notice, so a
  macOS update can break a source. When that happens the other sources keep working, the failing
  one reports "Apple changed the format of <source> on macOS <version>", and `python3 crm.py doctor`
  shows which one failed and why. Issues (with the `doctor` output) and PRs welcome.
- Paths can be overridden with environment variables if yours differ: `CRM_DB` (the database),
  `CRM_MESSAGES_DB`, `CRM_CONTACTS_GLOB`, `CRM_CALENDAR_DB`, `CRM_MAIL_GLOB`.
- macOS only. Linux and Windows have no equivalent data sources.

## Disclaimer

This is an independent personal project. It is not affiliated with, endorsed by, or supported
by Apple, Anthropic or LinkedIn. It needs Full Disk Access, which lets the granted app read
your private data, so read `crm.py` before running it (it's one file) and only install it from
a source you trust. Provided as-is under the MIT License, with no warranty.

## License

[MIT](LICENSE)
