from tests.support import CRMTestCase


class MessagesTest(CRMTestCase):
    synced = True

    def test_messages(self):
        lines = self.out("messages", "Alice Anders").splitlines()
        self.assertEqual([l.split(" ", 2)[2] for l in lines],
                         ["Alice: Are we still on for the ski trip?", "me: Yes! Booked the cabin",
                          "Alice: Typedstream hello from Alice"])  # oldest first; no tapback or system row
        self.assertRegex(lines[0], r"^\d{4}-\d\d-\d\d \d\d:\d\d Alice")

    def test_messages_options(self):
        out = self.out("messages", "Alice Anders", "--groups")
        self.assertIn("[Ski Trip] Alice: Ski trip packing list", out)
        self.assertEqual(self.out("messages", "alice", "--grep", "CABIN").count("\n"), 1)
        self.assertIn("Typedstream hello", self.out("messages", "alice", "--limit", "1"))
        self.assertEqual(self.out("messages", "alice", "--limit", "1").count("\n"), 1)
        self.assertIn("me: Sounds good", self.out("messages", "carol@personal.example"))

    def test_messages_without_history(self):
        self.assertIn("No Messages history", self.fails("messages", "Frank Foster"))
        self.out("add", "Nora Nohandle")
        self.assertIn("No phone/email for this person", self.fails("messages", "Nora Nohandle"))

    def test_grep(self):
        out = self.out("grep", "ski trip")
        self.assertRegex(out, r"\d{4}-\d\d-\d\d \[Ski Trip\] Alice Anders: \.\.\.Ski trip packing list")
        self.assertIn("Alice Anders: ...Are we still on for the ski trip?...", out)
        self.assertLess(out.index("[Ski Trip]"), out.index("still on"))  # newest first
        self.assertIn("me -> Alice Anders: ...Yes! Booked the cabin...", self.out("grep", "cabin"))
        self.assertIn("Typedstream hello from Alice", self.out("grep", "typedstream hello"))  # attributedBody only
        self.assertIn("[with Alice Anders, Bob Baker] Bob Baker: ...Lunch crew?...", self.out("grep", "lunch"))
        self.assertIn("+14155550177: ...Hi it's Pat", self.out("grep", "conference"))
        self.assertEqual(self.out("grep", "ski", "--limit", "1").count("\n"), 1)
        self.assertIn("No messages mention 'xylophone'.", self.out("grep", "xylophone"))


class EmailsTest(CRMTestCase):
    synced = True

    def test_emails(self):
        lines = self.out("emails", "Alice Anders").splitlines()
        heads = [l for l in lines if not l.startswith("    ")]
        self.assertEqual([h.split(" ", 1)[1] for h in heads],
                         ["me: Re: Propane pricing (+1 others)", "Alice: Q3 plan", "me: Re: Q3 plan"])
        self.assertIn("    Draft of the Q3 plan attached", lines)  # Mail's summary when no body requested

    def test_emails_bodies_and_grep(self):
        out = self.out("emails", "alice", "--bodies")
        self.assertIn("    Here is the Q3 plan we discussed. Main risk is the hiring timeline.", out)
        self.assertNotIn("quoted text", out)
        self.assertNotIn("wrote:", out)
        out = self.out("emails", "alice", "--grep", "propane")
        self.assertIn("Propane pricing", out)
        self.assertNotIn("Q3", out)
        self.assertEqual(self.out("emails", "alice", "--limit", "1").count(":"), 2)  # date line + summary

    def test_emails_skips_junk_and_drafts(self):
        self.out("link", "winner@junk.example", "Frank Foster")
        self.assertIn("No email found with this person in Mail.", self.out("emails", "Frank Foster"))
        self.out("link", "ina@inbound.example", "Carol Chen")
        out = self.out("emails", "Carol Chen")
        self.assertIn("Intro", out)
        self.assertNotIn("Unsent draft", out)

    def test_emails_without_address(self):
        self.out("add", "Nora Nohandle")
        self.assertIn("No email address for this person", self.fails("emails", "Nora Nohandle"))
