from tests.support import CRMTestCase


class PersonTest(CRMTestCase):
    synced = True

    def test_lookup_by_name_id_phone_email(self):
        alice = self.pid("Alice Anders")
        for q in ("Alice Anders", "  alice   anders ", "alice", str(alice), "+1 (415) 555-0101", "415-555-0101",
                  "4155550101", "ALICE@acme.example"):
            with self.subTest(q=q):
                self.assertTrue(self.out("person", q).startswith(f"[{alice}] Alice Anders\n"))

    def test_ambiguous_and_missing(self):
        out = self.fails("person", "Dave Dupe")
        self.assertIn("ambiguous; use an id", out)
        for pid in self.pids("Dave Dupe"):
            self.assertIn(f"[{pid}] Dave Dupe", out)
        self.assertIn("No person matches 'Zelda'", self.fails("person", "Zelda"))
        self.assertIn("No person matches '999999'", self.fails("person", "999999"))

    def test_person_details(self):
        self.out("note", "Alice Anders", "Met at the fake offsite")
        out = self.out("person", "Alice Anders")
        for line in ("contact card: Engineer, Acme Corp",
                     "phone: +14155550101 (contacts)  msgs in/out 2/1, group msgs 1",
                     "email: alice@acme.example (contacts)",
                     "works at (per contacts, as of ",
                     "last message from them: ", "(3d ago)",
                     "email in/out 1/2",
                     "shared group chats: Ski Trip [3]; with Bob Baker [2]",
                     "Project sync [2 others]",
                     "Coffee with Alice [1 others]",
                     "note ", "Met at the fake offsite"):
            self.assertIn(line, out)
        self.assertRegex(out, r"upcoming: \d{4}-\d\d-\d\d Project sync")

    def test_person_linkedin_and_declined(self):
        out = self.out("person", "Bob Baker")
        self.assertIn("linkedin: https://www.linkedin.com/in/bob-baker-fake (linkedin)", out)
        self.assertIn("works at (per linkedin, as of ", out)
        self.assertIn("Globex - Director of Product", out)
        self.assertIn("Pitch review [1 others] (they declined)", out)
        self.assertIn("last message to them:   ", out)

    def test_person_never_contacted(self):
        out = self.out("person", "Frank Foster")
        self.assertIn("last message from them: never", out)
        self.assertIn("frank@stark.example (linkedin)", out)

    def test_search(self):
        out = self.out("search", "ali")
        self.assertIn(f"[{self.pid('Alice Anders')}] Alice Anders - Acme Corp  | last msg ", out)
        self.assertIn("No matches.", self.out("search", "zzz"))
        self.assertIn("Carol Chen", self.out("search", "carol@personal.example"))

    def test_at(self):
        out = self.out("at", "acme")
        self.assertIn(f"[{self.pid('Alice Anders')}] Alice Anders - Engineer @ Acme Corp (per contacts as of", out)
        out = self.out("at", "Hooli")
        self.assertIn("Dave Dupe - ? @ Hooli (per contacts", out)
        self.assertIn("Hank Hill - email hank@hooli.example (domain match only; weak evidence)", out)
        self.assertIn("Erin Evans - Chief Revenue Officer @ Initrode (per linkedin", self.out("at", "initrode"))
        self.assertIn("Nobody found at 'Nowhere Inc'", self.out("at", "Nowhere Inc"))

    def test_catchup(self):
        self.assertIn("Nobody overdue", self.out("catchup"))
        self.out("set", "Bob Baker", "--relationship", "friend")
        self.out("set", "Alice Anders", "--relationship", "friend", "--cadence", "30")
        self.out("set", "Frank Foster", "--relationship", "work")
        out = self.out("catchup")
        self.assertIn("Bob Baker (friend, every 60d): no message/email/meeting found since ", out)
        self.assertNotIn("Alice", out)  # in touch 3 days ago
        self.assertLess(out.index("Frank Foster"), out.index("Bob Baker"))  # never contacted sorts first
        self.assertIn("Frank Foster (work, every 60d): no message/email/meeting found since never", out)
        self.assertNotIn("Frank", self.out("catchup", "--label", "friend"))
        self.out("set", "Alice Anders", "--cadence", "2")
        self.assertIn("Alice Anders (friend, every 2d)", self.out("catchup"))
        self.out("set", "Bob Baker", "--snooze", "2999-01-01")
        self.assertNotIn("Bob", self.out("catchup"))
        self.out("set", "Bob Baker", "--snooze", "2000-01-01")
        self.assertIn("Bob", self.out("catchup", "--default-days", "400"))
        self.assertNotIn("Bob", self.out("catchup", "--default-days", "500"))

    def test_catchup_upcoming_meeting(self):
        self.out("set", "Erin Evans", "--relationship", "work", "--cadence", "1")
        self.assertRegex(self.out("catchup"), r"Erin Evans \(work, every 1d\).*upcoming: \d{4}-\d\d-\d\d")

    def test_recent_and_top(self):
        recent = self.out("recent").splitlines()
        self.assertEqual([l.split("] ")[1].split(" (")[0] for l in recent], ["Alice Anders", "Carol Chen", "Bob Baker"])
        self.assertIn("(unlabeled) msgs 2/1 last ", recent[0])
        self.assertEqual(len(self.out("recent", "--limit", "1").splitlines()), 1)
        top = self.out("top").splitlines()
        self.assertEqual(top[0].split("] ")[1].split(" (")[0], "Alice Anders")
        self.assertEqual(len(top), 3)

    def test_unknown(self):
        out = self.out("unknown")
        self.assertIn("+14155550177: msgs 1/1, last ", out)
        self.assertNotIn("72975", out)  # business short code
        self.assertNotIn("+14155550188", out)  # inbound only
        self.out("link", "+14155550177", "Frank Foster")
        self.assertIn("No unknown two-way handles.", self.out("unknown"))
