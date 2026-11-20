import os
import sqlite3
import stat

from tests.support import CRMTestCase

SCHEMA_V0 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "schema_v0.sql")


class MigrationTest(CRMTestCase):
    def make_v0(self):
        """A crm.db as the first release left it: no user_version, old sync_log, world-readable."""
        os.makedirs(os.path.dirname(self.db_path))
        db = sqlite3.connect(self.db_path)
        with open(SCHEMA_V0) as f:
            db.executescript(f.read())
        db.execute("INSERT INTO people (id, name, relationship) VALUES (1, 'Olive Oldtimer', 'friend')")
        db.execute("INSERT INTO identities VALUES ('phone', '+14155550101', 1, 'manual', 1)")
        db.execute("INSERT INTO notes (person_id, text) VALUES (1, 'From the old days')")
        db.execute("INSERT INTO sync_log VALUES ('contacts', '2024-01-01 00:00:00', '3 cards from 1 address books')")
        db.commit()
        db.close()
        os.chmod(self.db_path, 0o644)

    def test_upgrade_from_first_release(self):
        self.make_v0()
        out = self.out("status")
        self.assertIn("contacts: last success 2024-01-01 00:00:00 UTC - 3 cards", out)
        cols = {r[1] for r in self.query("PRAGMA table_info(sync_log)")}
        self.assertTrue({"last_attempt_at", "last_error", "last_error_at"} <= cols)
        version = self.scalar("PRAGMA user_version")
        self.assertGreaterEqual(version, 1)
        self.assertEqual(stat.S_IMODE(os.stat(self.db_path).st_mode), 0o600)
        self.assertIn("note ", self.out("person", "Olive Oldtimer"))

        # Your data survives a full sync on the upgraded database; your manual link wins.
        self.out("sync", "--linkedin", self.fixtures["LINKEDIN_CSV"])
        self.assertEqual(self.scalar("SELECT person_id FROM identities WHERE value='+14155550101'"), 1)
        self.assertEqual(self.scalar("SELECT relationship FROM people WHERE id=1"), "friend")
        self.assertIn("From the old days", self.out("person", "1"))
        self.out("sync", "messages", env={"CRM_MESSAGES_DB": os.path.join(self.dir, "nope")})
        self.assertIn("not found", self.scalar("SELECT last_error FROM sync_log WHERE source='messages'"))
        self.out("status")
        self.assertEqual(self.scalar("PRAGMA user_version"), version)  # nothing re-runs

    def test_new_database_starts_at_latest_version(self):
        self.out("status")
        fresh = self.scalar("PRAGMA user_version")
        self.db_path = os.path.join(self.dir, "old", "crm.db")
        self.env["CRM_DB"] = self.db_path
        self.make_v0()
        self.out("status")
        self.assertEqual(self.scalar("PRAGMA user_version"), fresh)
        self.assertEqual({r[1] for r in self.query("PRAGMA table_info(sync_log)")}, {"source", "last_success_at", "detail", "last_attempt_at", "last_error",
                                  "last_error_at"})

    def test_views_are_rebuilt(self):
        self.make_v0()
        db = sqlite3.connect(self.db_path)
        db.execute("DROP VIEW v_people")
        db.execute("CREATE VIEW v_people AS SELECT 1 AS stale")
        db.commit()
        db.close()
        self.assertIn("Olive Oldtimer", self.out("search", "olive"))
