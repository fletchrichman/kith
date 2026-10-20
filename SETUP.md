# Getting your data in

kith doesn't connect to iCloud, Google or LinkedIn itself. It reads the copies that macOS apps
already keep on your Mac. So for each source, the job is to get the matching Apple app on this Mac
fully synced. Once Contacts, Messages, Calendar and Mail look right in their own apps, kith will
see the same data.

Work through the steps in order. After each one, run this from the kith folder:

```bash
python3 crm.py sync
```

Every source prints one line. A line that says `FAILED` tells you which step to revisit.
At any point, run the checkup:

```bash
python3 crm.py doctor
```

It tests Full Disk Access for each source, shows each source's last sync and last error, warns if
your Messages history covers less than about a year, if kith doesn't know your own email
addresses, or if no sent mail was found. Every warning links to the step below that fixes it.
`python3 crm.py status` is the short version: when each source last synced (and why it last
failed) and how far back your Messages history goes.

## 1. Full Disk Access (required)

macOS blocks apps from reading Messages, Mail and Calendar data until you allow it.

1. Open **System Settings > Privacy & Security > Full Disk Access**.
2. Turn it on for the app you run Claude Code in:
   - Claude desktop app: **Claude**
   - Terminal: **Terminal** (or **iTerm**, **Ghostty**, **Warp**)
   - VS Code / Cursor: **Visual Studio Code** / **Cursor**

   If the app isn't listed, click **+** and add it from Applications.
3. **Quit the app completely (⌘Q) and reopen it.** The permission doesn't apply until you do.

If you use more than one of these apps, grant it to each one.

**Signs it's missing:** `FAILED` mentioning "Full Disk Access", "unable to open database" or
"authorization denied", usually on messages, calendar or mail together.

## 2. Contacts

kith reads every address book the Contacts app shows (iCloud, Google, Exchange, "On My Mac").

1. Open **System Settings > Internet Accounts**. For each account whose contacts you want
   (iCloud, Google, work Exchange), click it and turn on **Contacts**.
2. Open the **Contacts** app and check that the people you expect are there. A new account
   can take a few minutes to download.

The same person saved in two accounts is merged automatically when the name and a phone number
or email match. Run `python3 crm.py dupes` to review the rest.

## 3. Messages (iMessage and SMS)

kith reads the Messages history stored on this Mac. History that only exists on your iPhone
isn't visible to it.

1. Open **Messages > Settings > iMessage** and sign in with the **same Apple ID as your iPhone**.
2. Turn on **Enable Messages in iCloud**, then click **Sync Now**. On your iPhone, also turn it on:
   **Settings > [your name] > iCloud > Messages in iCloud** (on some iOS versions it's under
   **Saved to iCloud** or **Show All**).
3. **Let it finish.** The first download of years of history can take hours. Keep the Mac awake
   and plugged in, with Messages open. The progress bar is at the bottom of
   Messages > Settings > iMessage.
4. **Green-bubble (SMS) texts:** on your iPhone go to **Settings > Apps > Messages > Text Message
   Forwarding** and turn on your Mac. Without this, only iMessages come through.

Check it: `python3 crm.py status` shows how far back your Messages history goes, for example
"spans 2017-11-25 to …". If it only goes back a few weeks, iCloud is still downloading or
Messages in iCloud is off.

Not included: WhatsApp, Signal, Slack and other chat apps.

## 4. Calendar

kith reads events from the Calendar app that have **other attendees with email addresses**. Solo
blocks and holidays are ignored.

1. Open **System Settings > Internet Accounts**. For your Google, iCloud or Exchange/Outlook
   account, turn on **Calendars**.
2. Open the **Calendar** app and check that your work calendar shows up, with attendees on the
   events.

Recurring meetings are counted occurrence by occurrence for about two years either side of today.
Calendar events are plans, not proof a meeting happened, and kith treats them that way.

**Signs it's missing:** the calendar line says `0 events with other attendees`, or your work
meetings don't appear in `python3 crm.py person <colleague>`. Usually the account isn't added,
or only its "Holidays" calendar is turned on.

## 5. Mail

kith reads Apple Mail's local index (senders, recipients and dates; no bodies) to see who you
email. **You need Apple Mail set up even if you normally use Gmail in the browser or Outlook.**
You don't have to start using Mail; it just needs to download your mail.

1. Open **System Settings > Internet Accounts**, click your Google / iCloud / Exchange account,
   and turn on **Mail**. Do this for each account you want counted, work and personal.
2. Open the **Mail** app and leave it open until it finishes downloading. A large mailbox can
   take hours the first time. The activity indicator is in the sidebar next to each account.
3. **Gmail only:** in Gmail on the web, go to **Settings > See all settings > Labels** and make
   sure **Sent Mail** and **All Mail** have "Show in IMAP" checked. Without Sent Mail, kith can't
   tell who you've written to.
4. Tell kith which addresses are yours:

   ```bash
   python3 crm.py me
   ```

   Add any that are missing (aliases, old work addresses):

   ```bash
   python3 crm.py me --add you@work.com
   ```

   Then run `python3 crm.py sync mail` again. An address missing from this list makes your sent
   mail look like received mail.

Newsletters, mailing lists and automated mail (no-reply, notifications) are skipped automatically.
`python3 crm.py emails <person> --bodies` reads opening lines live from Mail's downloaded copies.
It only works for messages Mail has fully downloaded.

## 6. LinkedIn (optional)

This adds current company and title for people you're connected with, including people you've
never messaged.

1. On LinkedIn, go to **Me > Settings & Privacy > Data privacy > Get a copy of your data**.
2. Choose **Want something in particular?**, tick **Connections**, and request the archive.
   The email usually arrives within about 10–30 minutes.
3. Download and unzip it, then import `Connections.csv`:

   ```bash
   python3 crm.py sync --linkedin ~/Downloads/Connections.csv
   ```

The export is a snapshot. kith records the export date and treats titles as "as of" then, so
re-import every few months. Many connections hide their email address, so LinkedIn people are
matched to your contacts by exact full name. Run `python3 crm.py dupes` afterwards to check.

## Keeping it fresh

There's nothing to schedule. When you ask a question and the data is more than 6 hours old,
kith re-syncs first, which takes about a second. Run `python3 crm.py sync` any time to force it.

## Troubleshooting

Start with `python3 crm.py doctor`; it checks most of these for you.

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Several sources `FAILED` with permission errors | No Full Disk Access, or the app wasn't restarted | Step 1, then quit and reopen the app |
| `contacts: FAILED - No AddressBook databases` | Contacts never opened, or no accounts enabled | Step 2 |
| Messages history only covers recent weeks | Messages in iCloud off or still downloading | Step 3; leave the Mac on overnight |
| Friends with Android phones never show up | Text Message Forwarding off | Step 3.4 |
| `calendar: 0 events with other attendees` | Work calendar not added to Calendar.app | Step 4 |
| `mail: FAILED - No Apple Mail index found` | Mail.app has never been set up | Step 5 |
| People you email show `0` sent | Sent folder hidden from IMAP, or your address isn't in `me` | Steps 5.3 and 5.4 |
| `Apple changed the format of <source> on macOS <version>` | A macOS update changed that app's private database | [Open an issue](https://github.com/fletchrichman/kith/issues) with the `doctor` output; the other sources keep working |
