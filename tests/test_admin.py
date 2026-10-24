import os
import sqlite3
import stat

from tests.support import CRMTestCase


class MeTest(CRMTestCase):
    synced = True

    def test_me(self):
        self.assertEqual(self.out("me"), "me@self.example (calendar)\n")
        self.assertEqual(self.out("me", "--add", "Me.Work@Self.example", "--add", "hank@hooli.example"),
                         "hank@hooli.example (manual)\nme.work@self.example (manual)\nme@self.example (calendar)\n")
        self.out("sync", "calendar")  # a calendar re-sync keeps manual addresses
        self.assertIn("me.work@self.example (manual)", self.out("me"))
        self.out("sync", "mail")  # mail from an address of mine is sent mail, not a correspondent
        self.assertFalse(self.query("SELECT 1 FROM email_stats WHERE address='hank@hooli.example'"))


class StatusDoctorTest(CRMTestCase):
    synced = True

    def test_status(self):
        out = self.out("status")
        for source in ("calendar", "contacts", "linkedin", "mail", "messages"):
            self.assertRegex(out, rf"{source}: last success \d{{4}}-\d\d-\d\d \d\d:\d\d:\d\d UTC - ")
        people = self.scalar("SELECT count(*) FROM v_people")
        unlinked = self.scalar("SELECT count(*) FROM identities WHERE person_id IS NULL")
        self.assertIn(f"{people} people, {unlinked} unlinked identities. Messages history on this Mac spans ", out)
        self.assertNotIn("FAILED", out)
        self.assertNotIn("Some sources are failing", out)

    def test_doctor_all_good(self):
        out = self.out("doctor")
        self.assertIn("kith doctor (macOS ", out)
        for s in ("readable (2 address books)", "history spans ", "your addresses: me@self.example",
                  "4 human correspondents, 4 sent emails counted", "last imported ", "(owner-only)"):
            self.assertIn(s, out)
        self.assertTrue(out.endswith("All good.\n"))
        self.assertNotIn("WARN", out)

    def test_doctor_problems(self):
        os.chmod(self.db_path, 0o644)
        env = {"CRM_CALENDAR_DB": os.path.join(self.dir, "missing", "Calendar.sqlitedb")}
        out = self.fails("doctor", env=env)
        self.assertIn("WARN  not set up: nothing at ", out)
        self.assertIn('fix: SETUP.md "4. Calendar" https://github.com/fletchrichman/kith/blob/main/SETUP.md#4-calendar',
                      out)
        self.assertIn("is readable by other users (mode 644)", out)
        self.assertIn("2 problem(s) found.", out)

    def test_doctor_format_change_and_failed_sync(self):
        cal = self.copy_fixture("CRM_CALENDAR_DB", "Calendar.sqlitedb")
        db = sqlite3.connect(cal)
        db.execute("ALTER TABLE Participant RENAME COLUMN is_self TO is_me")
        db.commit()
        db.close()
        env = {"CRM_CALENDAR_DB": cal}
        self.out("sync", "calendar", env=env)
        out = self.fails("doctor", env=env)
        self.assertIn("Apple changed the format of Calendar on macOS", out)
        self.assertIn("Participant has no is_self", out)
        self.assertIn('fix: SETUP.md "Troubleshooting"', out)
        out = self.fails("doctor")  # readable again, but the last sync failed
        self.assertIn("last sync FAILED ", out)
        self.assertIn("Some sources are failing", self.out("status"))

    def test_doctor_without_my_addresses_or_sent_mail(self):
        db = sqlite3.connect(self.db_path)
        db.execute("DELETE FROM self_addresses")
        db.execute("UPDATE email_stats SET n_out = 0")
        db.commit()
        db.close()
        out = self.fails("doctor")
        self.assertIn("no addresses of yours known", out)
        self.assertIn("people have emailed you but no sent mail was found", out)


class FreshDatabaseTest(CRMTestCase):
    def test_new_database_is_owner_only(self):
        self.out("status")
        self.assertEqual(stat.S_IMODE(os.stat(self.db_path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(self.db_path)).st_mode), 0o700)

    def test_doctor_never_synced(self):
        out = self.fails("doctor")
        self.assertIn("never synced. Run: python3 crm.py sync contacts", out)
        self.assertIn("not imported; see SETUP.md", out)

    def test_relative_db_path(self):
        self.out("sync", "contacts", env={"CRM_DB": "crm.db"})
        self.assertTrue(os.path.exists(os.path.join(self.dir, "crm.db")))
        self.assertIn("Alice Anders", self.out("search", "alice", env={"CRM_DB": "crm.db"}))

    def test_help(self):
        out = self.out("-h")
        for cmd in ("sync", "person", "search", "at", "catchup", "recent", "top", "unknown", "messages", "grep",
                    "emails", "note", "set", "add", "link", "merge", "dupes", "unmerge", "me", "status", "doctor", "sql"):
            self.assertIn(cmd, out)
        self.fails("bogus-command")


class SqlTest(CRMTestCase):
    synced = True

    def test_select(self):
        out = self.out("sql", "SELECT name, company FROM v_people WHERE name LIKE 'Alice%'")
        self.assertEqual(out, "name | company\nAlice Anders | Acme Corp\n")
        self.assertEqual(len(self.out("sql", "SELECT id FROM people", "--limit", "2").splitlines()), 3)
        self.assertIn("name", self.out("sql", "PRAGMA table_info(people)"))
        self.assertIn("v_people", self.out("sql", "WITH t AS (SELECT name FROM sqlite_master) SELECT * FROM t"))

    def test_read_only(self):
        people = self.scalar("SELECT count(*) FROM people")
        version = self.scalar("PRAGMA user_version")
        for q in ("DELETE FROM people", "UPDATE people SET name='x'", "INSERT INTO notes (text) VALUES ('x')",
                  "DROP TABLE notes", "CREATE TABLE evil (x)", "PRAGMA user_version = 99",
                  "SELECT 1; DELETE FROM people", "VACUUM"):
            with self.subTest(q=q):
                self.assertIn("sql: ", self.fails("sql", q))
        self.assertEqual(self.scalar("SELECT count(*) FROM people"), people)
        self.assertEqual(self.scalar("PRAGMA user_version"), version)
        self.assertIsNotNone(self.scalar("SELECT 1 FROM sqlite_master WHERE name='notes'"))

    def test_attach_is_refused(self):
        other = os.path.join(self.dir, "other.db")
        self.assertIn("sql: ", self.fails("sql", f"ATTACH DATABASE '{other}' AS o"))
        self.assertFalse(os.path.exists(other))
