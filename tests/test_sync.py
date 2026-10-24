import os
import sqlite3
import shutil
import time

from tests.support import CRMTestCase


class SyncTest(CRMTestCase):
    def test_contacts(self):
        self.assertIn("contacts: 12 cards from 2 address books", self.out("sync", "contacts"))
        alice = self.pid("Alice Anders")  # the iCloud and Google cards share a phone, so one person
        self.assertEqual({(r["kind"], r["value"]) for r in self.query(
            "SELECT kind, value FROM identities WHERE person_id=?", alice)},
            {("phone", "+14155550101"), ("email", "alice@acme.example"), ("email", "alice.personal@home.example")})
        self.assertEqual(self.scalar("SELECT count(*) FROM source_keys WHERE person_id=?", alice), 2)
        self.assertEqual(len(self.pids("Dave Dupe")), 2)  # same name, nothing shared: an honest duplicate
        self.assertEqual({r[0] for r in self.query("SELECT value FROM identities WHERE kind='phone'")},
                         {"+14155550101", "+14155550102", "+14155550103", "+14155550141", "+14155550199",
                          "+14155550161", "+14155550171", "+14155550142", "+14155550162", "+14155550172"})
        self.assertFalse(self.query("SELECT 1 FROM people WHERE name='Nobody'"))  # no name or company: skipped
        company_card = self.pid("Acme Corp")
        self.assertFalse(self.query("SELECT 1 FROM affiliations WHERE person_id=?", company_card))
        aff = self.query("SELECT org, title, source, as_of FROM affiliations WHERE person_id=?", alice)
        self.assertEqual([(r["org"], r["title"], r["source"]) for r in aff], [("Acme Corp", "Engineer", "contacts")])
        self.assertRegex(aff[0]["as_of"], r"^\d{4}-\d\d-\d\d$")

        before = self.scalar("SELECT count(*) FROM people")
        self.out("sync", "contacts")
        self.assertEqual(self.scalar("SELECT count(*) FROM people"), before)  # re-sync is idempotent

    def test_messages(self):
        self.assertIn("messages: 6 handles, 2 group chats", self.out("sync", "messages"))
        alice = self.query("SELECT * FROM msg_stats WHERE handle='+14155550101'")[0]
        # Text + attributedBody-only message count; the tapback and the system row don't.
        self.assertEqual((alice["dm_in"], alice["dm_out"], alice["group_msgs"]), (2, 1, 1))
        self.assertLess(alice["last_out_at"], alice["last_in_at"])
        bob = self.query("SELECT * FROM msg_stats WHERE handle='+14155550102'")[0]
        self.assertEqual((bob["dm_in"], bob["dm_out"], bob["group_msgs"]), (1, 1, 2))
        self.assertIn("carol@personal.example", {r[0] for r in self.query("SELECT handle FROM msg_stats")})
        self.assertEqual({(r["name"], r["size"]) for r in self.query("SELECT name, size FROM group_chats")},
                         {("Ski Trip", 3), (None, 2)})
        self.assertEqual(self.scalar("SELECT count(*) FROM group_members"), 5)
        # Every handle becomes an identity (unlinked until Contacts or you claim it).
        self.assertIsNone(self.scalar("SELECT person_id FROM identities WHERE value='+14155550177'"))
        self.assertEqual(self.scalar("SELECT kind FROM identities WHERE value='carol@personal.example'"), "email")

    def test_calendar(self):
        self.out("sync", "contacts")
        self.assertIn("calendar: 5 events with other attendees", self.out("sync", "calendar"))
        self.assertEqual([tuple(r) for r in self.query("SELECT * FROM self_addresses")],
                         [("me@self.example", "calendar")])
        rows = self.query("SELECT title, email, status, n_attendees, start_at FROM events")
        by_title = {}
        for r in rows:
            by_title.setdefault(r["title"], []).append(r)
        self.assertNotIn("Dentist", by_title)
        self.assertEqual({r["email"] for r in by_title["Project sync"]},
                         {"alice@acme.example", "erin@initrode.example"})  # the room is not a person
        self.assertEqual(len({r["start_at"] for r in by_title["1:1 Erin"]}), 3)  # one row per occurrence
        self.assertEqual(by_title["Pitch review"][0]["status"], "3")
        self.assertEqual(by_title["All hands"][0]["n_attendees"], 13)
        self.assertNotIn("me@self.example", {r["email"] for r in rows})

        alice = self.query("SELECT * FROM v_people WHERE name='Alice Anders'")[0]
        coffee = by_title["Coffee with Alice"][0]["start_at"]
        self.assertEqual(alice["last_event_at"], coffee)  # the 13-person all hands doesn't count as contact
        self.assertEqual(alice["next_event_at"], by_title["Project sync"][0]["start_at"])
        self.assertIsNone(self.query("SELECT * FROM v_people WHERE name='Bob Baker'")[0]["last_event_at"])  # declined

    def test_mail(self):
        self.out("sync", "contacts", "calendar")
        self.assertIn("mail: 4 human correspondents, 3 you've written to, 1 new people", self.out("sync", "mail"))
        stats = {r["address"]: r for r in self.query("SELECT * FROM email_stats")}
        self.assertNotIn("stale@old.example", stats)  # V10 wins over the stale V9 index
        for bot in ("noreply@service.example", "digest@lists.example", "winner@junk.example", "me@self.example"):
            self.assertNotIn(bot, stats)
        self.assertEqual((stats["alice@acme.example"]["n_in"], stats["alice@acme.example"]["n_out"]), (1, 2))
        self.assertEqual(stats["ina@inbound.example"]["n_out"], 0)  # a draft is not sent mail
        hank = self.pid("Hank Hill")  # two-way correspondent not in Contacts, named from the header
        self.assertEqual(self.scalar("SELECT source FROM identities WHERE value='hank@hooli.example'"), "mail")
        self.assertEqual(self.scalar("SELECT person_id FROM identities WHERE value='hank@hooli.example'"), hank)
        self.assertIsNone(self.scalar("SELECT 1 FROM identities WHERE value='ina@inbound.example'"))
        self.assertFalse(self.query("SELECT 1 FROM people WHERE name='Madonna'"))  # single names aren't created
        self.assertIsNone(self.scalar("SELECT person_id FROM identities WHERE value='mononym@single.example'"))
        self.assertIn("me@self.example", {r[0] for r in self.query("SELECT address FROM self_addresses")})

    def test_mail_without_trash_junk_or_drafts_mailboxes(self):
        mail = os.path.join(self.dir, "Mail")
        shutil.copytree(os.path.dirname(os.path.dirname(os.path.dirname(
            self.fixtures["CRM_MAIL_GLOB"].replace("V*", "V10")))), mail)
        db = sqlite3.connect(os.path.join(mail, "V10", "MailData", "Envelope Index"))
        skip = "SELECT ROWID FROM mailboxes WHERE url LIKE '%Junk' OR url LIKE '%Trash' OR url LIKE '%Drafts'"
        db.execute(f"DELETE FROM messages WHERE mailbox IN ({skip})")
        db.execute(f"DELETE FROM mailboxes WHERE ROWID IN ({skip})")
        db.commit()
        db.close()
        env = {"CRM_MAIL_GLOB": os.path.join(mail, "V*", "MailData", "Envelope Index")}
        self.out("sync", "calendar")
        self.assertIn("mail: 4 human correspondents", self.out("sync", "mail", env=env))

    def test_linkedin(self):
        self.out("sync", "contacts")
        csv = self.fixtures["LINKEDIN_CSV"]
        self.assertRegex(self.out("sync", "--linkedin", csv), r"linkedin: 2 matched, 1 new \(snapshot as of \d{4}")
        bob = self.pid("Bob Baker")  # matched by unique exact name
        self.assertEqual(self.scalar("SELECT person_id FROM identities WHERE kind='linkedin'"
                                     " AND value='https://www.linkedin.com/in/bob-baker-fake'"), bob)
        self.assertEqual(self.scalar("SELECT title FROM affiliations WHERE person_id=? AND source='linkedin'", bob),
                         "Director of Product")
        frank = self.pid("Frank Foster")
        self.assertEqual(self.scalar("SELECT person_id FROM identities WHERE value='frank@stark.example'"), frank)
        self.assertEqual(len(self.pids("Erin Evans")), 1)  # matched by email, not duplicated

        newer = os.path.join(self.dir, "Connections.csv")
        with open(csv, encoding="utf-8-sig") as f, open(newer, "w", encoding="utf-8") as g:
            g.write(f.read().replace("Globex,Director of Product", "Umbrella,VP Product"))
        self.out("sync", "--linkedin", newer)
        self.assertEqual({(r["org"], r["source"]) for r in self.query(
            "SELECT org, source FROM affiliations WHERE person_id=? AND source LIKE 'linkedin%'", bob)},
            {("Umbrella", "linkedin"), ("Globex", "linkedin (former)")})
        self.assertEqual(self.scalar("SELECT count(*) FROM people WHERE name='Frank Foster'"), 1)

    def test_linkedin_without_header_fails_cleanly(self):
        bad = os.path.join(self.dir, "bad.csv")
        with open(bad, "w") as f:
            f.write("not,a,linkedin,export\n")
        out = self.out("sync", "--linkedin", bad)
        self.assertIn("linkedin: FAILED", out)
        self.assertIsNotNone(self.scalar("SELECT last_error FROM sync_log WHERE source='linkedin'"))

    def test_failed_source_is_logged_and_cleared_by_next_success(self):
        out = self.out("sync", "messages", env={"CRM_MESSAGES_DB": os.path.join(self.dir, "nope.db")})
        self.assertIn("messages: FAILED - not found:", out)
        self.assertIn("python3 crm.py doctor", out)
        row = self.query("SELECT * FROM sync_log WHERE source='messages'")[0]
        self.assertIsNone(row["last_success_at"])
        self.assertIn("not found", row["last_error"])
        self.assertIn("last attempt FAILED", self.out("status"))
        self.out("sync", "messages")
        row = self.query("SELECT * FROM sync_log WHERE source='messages'")[0]
        self.assertIsNotNone(row["last_success_at"])
        self.assertIsNone(row["last_error"])

    def test_failure_keeps_previous_data(self):
        self.out("sync", "messages")
        n = self.scalar("SELECT count(*) FROM msg_stats")
        self.out("sync", "messages", env={"CRM_MESSAGES_DB": os.path.join(self.dir, "nope.db")})
        self.assertEqual(self.scalar("SELECT count(*) FROM msg_stats"), n)
        self.assertIsNotNone(self.scalar("SELECT last_success_at FROM sync_log WHERE source='messages'"))

    def test_format_change_is_reported_per_source(self):
        chat = self.copy_fixture("CRM_MESSAGES_DB", "chat.db")
        db = sqlite3.connect(chat)
        db.execute("ALTER TABLE handle RENAME TO handle_v2")
        db.commit()
        db.close()
        out = self.out("sync", env={"CRM_MESSAGES_DB": chat})
        self.assertIn("messages: FAILED - Apple changed the format of Messages", out)
        self.assertIn("table handle is missing", out)
        self.assertIn("contacts: 12 cards", out)  # the other sources still sync
        self.assertIn("mail: 4 human correspondents", out)

    def test_unknown_source_is_rejected(self):
        self.assertIn("unknown source(s): bogus", self.fails("sync", "bogus"))

    def test_auto_sync_when_stale(self):
        env = {"CRM_NO_AUTOSYNC": ""}
        p = self.crm("search", "Alice", env=env)
        self.assertIn("auto-synced", p.stderr)
        self.assertIn("Alice Anders", p.stdout)
        p = self.crm("search", "Alice", env=env)
        self.assertNotIn("auto-synced", p.stderr)  # fresh now
        db = sqlite3.connect(self.db_path)
        db.execute("UPDATE sync_log SET last_attempt_at = datetime('now', '-7 hours')")
        db.commit()
        db.close()
        self.assertIn("auto-synced", self.crm("search", "Alice", env=env).stderr)

    def test_auto_sync_reports_failures(self):
        p = self.crm("recent", env={"CRM_NO_AUTOSYNC": "", "CRM_CALENDAR_DB": os.path.join(self.dir, "x")})
        self.assertIn("failed: calendar", p.stderr)
        self.assertIn("Alice Anders", p.stdout)
