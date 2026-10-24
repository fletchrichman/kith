import json

from tests.support import CRMTestCase


class MergeTest(CRMTestCase):
    synced = True

    def test_dupes_lists_evidence(self):
        out = self.out("dupes")
        a, b = self.pids("Dave Dupe")
        self.assertIn("Dave Dupe\n", out)
        self.assertIn(f"[{a}] cards=1 sources=contacts orgs=Initech msgs=0 last=never", out)
        self.assertIn(f"[{b}] cards=1 sources=contacts orgs=Hooli", out)
        self.assertIn("Albert\n", out)
        self.assertIn("Ivy Irwin\n", out)
        self.assertNotIn("Alice", out)

    def test_dupes_auto_merges_only_safe_pairs(self):
        card = self.pid("Carol Chen")
        self.out("add", "Carol Chen", "--handle", "carol.work@fake.example")
        ivy = self.pids("Ivy Irwin")
        out = self.out("dupes", "--auto")
        self.assertIn("Auto-merged 2 pairs (undo any with: unmerge <id>). 2 groups left for review.", out)
        self.assertEqual(self.pids("Carol Chen"), [card])  # the contact card is kept
        self.assertEqual(self.scalar("SELECT person_id FROM identities WHERE value='carol.work@fake.example'"), card)
        self.assertEqual(len(self.pids("Ivy Irwin")), 1)
        self.assertEqual(len(self.pids("Dave Dupe")), 2)  # employers disagree
        self.assertEqual(len(self.pids("Albert")), 2)  # single names are never auto-merged
        self.assertEqual(self.scalar("SELECT count(*) FROM merge_log WHERE reason LIKE 'auto:%'"), 2)
        self.assertNotIn("Ivy Irwin", out.split("groups left for review.")[1])
        self.assertIn(f"Unmerged person {ivy[1]} from {ivy[0]}.", self.out("unmerge", str(ivy[1])))
        self.assertEqual(self.pids("Ivy Irwin"), ivy)

    def test_no_dupes(self):
        for keep, drop in (self.pids("Dave Dupe"), self.pids("Albert"), self.pids("Ivy Irwin")):
            self.out("merge", str(keep), str(drop))
        self.assertIn("No same-name duplicates.", self.out("dupes"))

    def test_merge_unmerge_round_trip(self):
        keep, drop = self.pids("Dave Dupe")
        self.out("note", str(drop), "Plays bass")
        self.out("set", str(drop), "--relationship", "friend", "--cadence", "14", "--title", "Bassist")
        before = {t: self.query(f"SELECT * FROM {t} ORDER BY 1, 2") for t in
                  ("identities", "notes", "source_keys", "affiliations", "people")}

        self.assertIn(f"Merged [{drop}] Dave Dupe into [{keep}] Dave Dupe. Undo: unmerge {drop}",
                      self.out("merge", str(keep), str(drop)))
        self.assertEqual(self.pids("Dave Dupe"), [keep])
        out = self.out("person", str(drop))  # the old id follows the merge
        self.assertTrue(out.startswith(f"[{keep}] Dave Dupe"))
        for s in ("dave@hooli.example", "dave@initech.example", "Plays bass", "Hooli", "Initech",
                  "relationship: friend, wants contact every 14d"):
            self.assertIn(s, out)
        moved = json.loads(self.scalar("SELECT moved_json FROM merge_log WHERE drop_id=?", drop))
        self.assertIn(["email", "dave@hooli.example"], moved["identities"])
        self.out("sync")  # the dropped card's source key now points at the kept person
        self.assertEqual(self.pids("Dave Dupe"), [keep])

        self.out("unmerge", str(drop))
        after = {t: self.query(f"SELECT * FROM {t} ORDER BY 1, 2") for t in before}
        for t in ("identities", "notes", "source_keys", "affiliations"):
            self.assertEqual([tuple(r) for r in after[t]], [tuple(r) for r in before[t]], t)
        people = lambda rows: {r["id"]: (r["relationship"], r["cadence_days"], r["title"], r["merged_into"])
                               for r in rows if r["id"] in (keep, drop)}
        self.assertEqual(people(after["people"]), people(before["people"]))
        self.assertIsNotNone(self.scalar("SELECT undone_at FROM merge_log WHERE drop_id=?", drop))
        self.assertIn(f"No active merge for person {drop}", self.fails("unmerge", str(drop)))

    def test_unmerge_keeps_edits_made_after_the_merge(self):
        keep, drop = self.pids("Dave Dupe")
        self.out("set", str(drop), "--relationship", "friend")
        self.out("merge", str(keep), str(drop))
        self.out("set", str(keep), "--relationship", "work")
        self.out("unmerge", str(drop))
        self.assertEqual(self.scalar("SELECT relationship FROM people WHERE id=?", keep), "work")
        self.assertEqual(self.scalar("SELECT relationship FROM people WHERE id=?", drop), "friend")

    def test_merge_errors(self):
        alice = self.pid("Alice Anders")
        self.assertIn("Same person.", self.fails("merge", str(alice), "Alice Anders"))
        self.fails("merge", str(alice), "Dave Dupe")  # ambiguous
        self.assertIn("No active merge for person 424242", self.fails("unmerge", "424242"))
