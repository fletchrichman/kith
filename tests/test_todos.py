import json
import sqlite3
import unittest
from datetime import date, timedelta

from kith.commands.todos import parse_due
from tests.support import CRMTestCase


class ParseDueTest(unittest.TestCase):
    friday = date(2026, 11, 20)

    def test_forms(self):
        cases = {
            "2026-12-31": "2026-12-31", "today": "2026-11-20", "Tomorrow": "2026-11-21",
            "saturday": "2026-11-21", "mon": "2026-11-23", "Thurs": "2026-11-26",
            "friday": "2026-11-27",  # the next one, never today
            "+3d": "2026-11-23", "+0d": "2026-11-20", "+2w": "2026-12-04", "+45d": "2027-01-04",
            " +1W ": "2026-11-27", "none": None, "": None,
        }
        for s, want in cases.items():
            self.assertEqual(parse_due(s, self.friday), want, s)

    def test_rejects(self):
        for s in ("2026-13-01", "next week", "fr", "+3", "3d", "+2m", "11/20"):
            with self.assertRaises(SystemExit, msg=s):
                parse_due(s, self.friday)


class TodoTest(CRMTestCase):
    synced = True

    def ids(self, out):
        return [int(line.split()[0][1:]) for line in out.splitlines() if line.startswith("#")]

    def add(self, *args):
        self.out("todo", "add", *args)
        return self.scalar("SELECT max(id) FROM todos")

    def test_crud(self):
        self.assertIn("No open todos.", self.out("todo"))
        out = self.out("todo", "add", "Book the venue", "--due", "2030-01-15")
        self.assertIn("Added #1 Book the venue  (due 2030-01-15 (Tue, in ", out)
        row = self.query("SELECT * FROM todos WHERE id=1")[0]
        self.assertEqual((row["text"], row["due"], row["person_id"], row["done_at"]),
                         ("Book the venue", "2030-01-15", None, None))
        self.assertIsNotNone(row["created_at"])

        self.assertIn("Done: #1 done ", self.out("todo", "done", "1"))
        self.assertIsNotNone(self.scalar("SELECT done_at FROM todos WHERE id=1"))
        self.assertIn("#1 is already done.", self.fails("todo", "done", "1"))
        self.assertIn("No open todos.", self.out("todo"))
        self.assertIn("Reopened: #1 Book the venue", self.out("todo", "undo", "1"))
        self.assertIsNone(self.scalar("SELECT done_at FROM todos WHERE id=1"))
        self.assertIn("#1 is not done.", self.fails("todo", "undo", "1"))

        self.assertIn("Updated #1 Book the bigger venue  (Carol Chen", self.out(
            "todo", "edit", "1", "--text", "Book the bigger venue", "--due", "none", "--person", "Carol Chen"))
        row = self.query("SELECT * FROM todos WHERE id=1")[0]
        self.assertEqual((row["text"], row["due"], row["person_id"]),
                         ("Book the bigger venue", None, self.pid("Carol Chen")))
        self.out("todo", "edit", "1", "--person", "none", "--due", "tomorrow")
        row = self.query("SELECT * FROM todos WHERE id=1")[0]
        self.assertEqual((row["person_id"], row["due"]), (None, (date.today() + timedelta(days=1)).isoformat()))
        self.assertIn("Nothing to change", self.fails("todo", "edit", "1"))

        self.assertIn("Removed #1 Book the bigger venue", self.out("todo", "rm", "1"))
        self.assertEqual(self.scalar("SELECT count(*) FROM todos"), 0)
        for action in ("done", "undo", "rm"):
            self.assertIn("No todo #1.", self.fails("todo", action, "1"))
        self.assertIn("No todo #1.", self.fails("todo", "edit", "1", "--text", "x"))

    def test_bad_input_changes_nothing(self):
        self.assertIn("Can't read due date 'someday'", self.fails("todo", "add", "x", "--due", "someday"))
        self.fails("todo", "add", "x", "--person", "Dave Dupe")  # ambiguous
        self.fails("todo", "add", "x", "--person", "Nobody Atall")
        self.assertEqual(self.scalar("SELECT count(*) FROM todos"), 0)
        tid = self.add("Keep me", "--due", "2030-01-01")
        self.fails("todo", "edit", str(tid), "--text", "Changed", "--due", "someday")
        self.assertEqual(tuple(self.query("SELECT text, due FROM todos")[0]), ("Keep me", "2030-01-01"))

    def test_ordering(self):
        today = date.today()
        day = lambda n: (today + timedelta(days=n)).isoformat()
        undated_old = self.add("Undated, older")
        later = self.add("Later", "--due", day(10))
        overdue = self.add("Overdue", "--due", day(-3))
        due_today = self.add("Today", "--due", "today")
        undated_new = self.add("Undated, newer")
        very_overdue = self.add("Very overdue", "--due", day(-30))
        tomorrow = self.add("Tomorrow", "--due", "tomorrow")
        done = self.add("Finished", "--due", day(-1))
        self.out("todo", "done", str(done))

        out = self.out("todo")
        self.assertEqual(self.ids(out), [very_overdue, overdue, due_today, tomorrow, later, undated_old, undated_new])
        self.assertIn(f"#{overdue} Overdue  (due {day(-3)} (3d overdue))", out)
        self.assertIn(f"#{due_today} Today  (due {day(0)} (today))", out)
        self.assertIn(f"#{tomorrow} Tomorrow  (due {day(1)} (tomorrow))", out)
        self.assertIn(f"in 10d))", out)
        self.assertIn(f"#{undated_old} Undated, older\n", out)

        self.assertEqual(self.ids(self.out("todo", "--all")), self.ids(out) + [done])
        done_out = self.out("todo", "--done")
        self.assertEqual(self.ids(done_out), [done])
        self.assertNotIn("overdue", done_out)
        self.assertIn(f"#{done} done {date.today().isoformat()[:4]}", done_out)

        status = self.out("status")
        self.assertIn("7 open todos (2 overdue, 1 due today). Run `python3 crm.py todo` to see them.", status)

    def test_status_is_quiet_without_todos(self):
        self.assertNotIn("todos", self.out("status"))
        tid = self.add("Someday")
        self.assertIn("1 open todos. Run", self.out("status"))
        self.out("todo", "done", str(tid))
        self.assertNotIn("todos", self.out("status"))

    def test_person_linkage(self):
        carol, frank = self.pid("Carol Chen"), self.pid("Frank Foster")
        a = self.add("Send Carol the deck", "--person", "carol", "--due", "2030-02-01")
        b = self.add("Intro Carol to Frank", "--person", str(carol))
        f = self.add("Ask Frank about skis", "--person", "Frank Foster")
        loose = self.add("Buy stamps")
        done = self.add("Thank Carol", "--person", "Carol Chen")
        self.out("todo", "done", str(done))
        self.assertEqual(self.scalar("SELECT person_id FROM todos WHERE id=?", a), carol)
        self.assertIsNone(self.scalar("SELECT person_id FROM todos WHERE id=?", loose))

        out = self.out("todo", "--person", "Carol Chen")
        self.assertEqual(self.ids(out), [a, b])
        self.assertIn(f"#{a} Send Carol the deck  (due 2030-02-01 (", out)
        self.assertIn(f"; Carol Chen [{carol}])", out)
        self.assertEqual(self.ids(self.out("todo", "--person", "carol", "--all")), [a, b, done])
        self.assertIn(f"Ask Frank about skis  (Frank Foster [{frank}])", self.out("todo"))

        person = self.out("person", "Carol Chen")
        self.assertIn(f"  todo #{a} Send Carol the deck  (due 2030-02-01 (", person)
        self.assertIn(f"  todo #{b} Intro Carol to Frank\n", person)
        self.assertNotIn("Thank Carol", person)
        self.assertNotIn("Buy stamps", person)
        self.assertNotIn("todo #", self.out("person", "Alice Anders"))

    def test_merge_and_unmerge_carry_todos(self):
        keep, drop = self.pids("Dave Dupe")
        kept_todo = self.add("Kept Dave's todo", "--person", str(keep))
        open_todo = self.add("Dropped Dave's todo", "--person", str(drop), "--due", "2030-03-01")
        done_todo = self.add("Dropped Dave's done todo", "--person", str(drop))
        self.out("todo", "done", str(done_todo))

        self.out("merge", str(keep), str(drop))
        moved = json.loads(self.scalar("SELECT moved_json FROM merge_log WHERE drop_id=?", drop))
        self.assertEqual(sorted(moved["todos"]), [open_todo, done_todo])
        self.assertEqual({r[0] for r in self.query("SELECT person_id FROM todos")}, {keep})
        out = self.out("person", str(drop))  # follows the merge to the kept person
        self.assertIn(f"todo #{open_todo} Dropped Dave's todo", out)
        self.assertIn(f"todo #{kept_todo} Kept Dave's todo", out)
        self.assertEqual(self.ids(self.out("todo", "--person", str(keep), "--all")), [open_todo, kept_todo, done_todo])
        after_merge = self.add("Added after the merge", "--person", str(drop))  # lands on the kept person
        self.assertEqual(self.scalar("SELECT person_id FROM todos WHERE id=?", after_merge), keep)

        self.out("unmerge", str(drop))
        owners = {r["id"]: r["person_id"] for r in self.query("SELECT id, person_id FROM todos")}
        self.assertEqual(owners, {kept_todo: keep, open_todo: drop, done_todo: drop, after_merge: keep})

    def test_unmerge_leaves_todos_you_moved_since(self):
        keep, drop = self.pids("Dave Dupe")
        carol = self.pid("Carol Chen")
        tid = self.add("Ask Dave", "--person", str(drop))
        self.out("merge", str(keep), str(drop))
        self.out("todo", "edit", str(tid), "--person", "Carol Chen")
        self.out("unmerge", str(drop))
        self.assertEqual(self.scalar("SELECT person_id FROM todos WHERE id=?", tid), carol)

    def test_unmerge_of_a_merge_logged_before_todos(self):
        keep, drop = self.pids("Dave Dupe")
        self.out("merge", str(keep), str(drop))
        moved = json.loads(self.scalar("SELECT moved_json FROM merge_log WHERE drop_id=?", drop))
        del moved["todos"]
        db = sqlite3.connect(self.db_path)
        db.execute("UPDATE merge_log SET moved_json=? WHERE drop_id=?", (json.dumps(moved), drop))
        db.commit()
        db.close()
        self.assertIn(f"Unmerged person {drop} from {keep}.", self.out("unmerge", str(drop)))
