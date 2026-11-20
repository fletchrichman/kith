from tests.support import CRMTestCase


class WriteTest(CRMTestCase):
    synced = True

    def test_note(self):
        self.assertIn("Noted on Carol Chen.", self.out("note", "carol", "Moved to Denver"))
        self.out("note", "Carol Chen", "Has two kids")
        self.assertEqual([r[0] for r in self.query("SELECT text FROM notes WHERE person_id=? ORDER BY id",
                                                   self.pid("Carol Chen"))], ["Moved to Denver", "Has two kids"])
        self.fails("note", "Dave Dupe", "which one?")
        self.assertEqual(self.scalar("SELECT count(*) FROM notes"), 2)

    def test_set(self):
        carol = self.pid("Carol Chen")
        self.assertIn("Updated Carol Chen.", self.out("set", "Carol Chen", "--relationship", "friend", "--cadence",
                                                       "45", "--snooze", "2030-01-01", "--company", "Wayne Enterprises",
                                                       "--title", "CFO"))
        row = self.query("SELECT * FROM people WHERE id=?", carol)[0]
        self.assertEqual((row["relationship"], row["cadence_days"], row["snoozed_until"], row["company"],
                          row["title"]), ("friend", 45, "2030-01-01", "Wayne Enterprises", "CFO"))
        self.out("set", str(carol), "--relationship", "none", "--snooze", "", "--name", "Carol Chen-Li")
        row = self.query("SELECT * FROM people WHERE id=?", carol)[0]
        self.assertEqual((row["relationship"], row["snoozed_until"], row["name"], row["cadence_days"]),
                         (None, None, "Carol Chen-Li", 45))
        self.out("sync", "contacts")  # a re-sync keeps your edits
        self.assertEqual(self.query("SELECT name FROM people WHERE id=?", carol)[0][0], "Carol Chen-Li")
        self.assertIn("relationship: -, wants contact every 45d", self.out("person", str(carol)))

    def test_add(self):
        out = self.out("add", "Pat Stranger", "--handle", "(415) 555-0177", "--handle", "Pat@Conf.example",
                       "--company", "Conf Co", "--title", "Organizer", "--relationship", "work")
        pat = self.pid("Pat Stranger")
        self.assertIn(f"Added [{pat}] Pat Stranger.", out)
        self.assertEqual({(r["kind"], r["value"], r["manual"]) for r in self.query(
            "SELECT kind, value, manual FROM identities WHERE person_id=?", pat)},
            {("phone", "+14155550177", 1), ("email", "pat@conf.example", 1)})
        self.assertIn("No unknown two-way handles.", self.out("unknown"))
        self.out("sync")
        self.assertEqual(self.scalar("SELECT person_id FROM identities WHERE value='+14155550177'"), pat)
        self.assertIn("msgs in/out 1/1", self.out("person", "Pat Stranger"))

    def test_link(self):
        frank = self.pid("Frank Foster")
        self.assertIn("Linked +14155550177 -> Frank Foster.", self.out("link", "415 555 0177", "Frank Foster"))
        self.out("link", "Frank.Personal@Home.example", "Frank Foster")
        self.out("link", "https://www.linkedin.com/in/Frank-Alt", "Frank Foster")
        self.out("link", "alice.personal@home.example", "Frank Foster")  # moves it off Alice
        kinds = {(r["kind"], r["value"]) for r in self.query(
            "SELECT kind, value FROM identities WHERE person_id=? AND manual=1", frank)}
        self.assertEqual(kinds, {("phone", "+14155550177"), ("email", "frank.personal@home.example"),
                                 ("linkedin", "https://www.linkedin.com/in/frank-alt"),
                                 ("email", "alice.personal@home.example")})
        self.out("sync")  # Contacts still lists that email on Alice's card; your link wins
        self.assertEqual(self.scalar("SELECT person_id FROM identities WHERE value='alice.personal@home.example'"),
                         frank)
        self.fails("link", "+14155550177", "Nobody Atall")
