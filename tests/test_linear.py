import os
import time

from tests.fake_linear import KEY, FakeLinear
from tests.support import CRMTestCase


class LinearTest(CRMTestCase):
    synced = True

    def setUp(self):
        super().setUp()
        self.linear = FakeLinear()
        self.addCleanup(self.linear.stop)
        self.env.update(CRM_LINEAR_URL=self.linear.url, LINEAR_API_KEY=KEY)

    def setup_linear(self, *extra):
        return self.out("todo", "linear", "setup", "--team", "eng", *extra)

    def todo(self, tid):
        return self.query("SELECT * FROM todos WHERE id=?", tid)[0]

    def add(self, *args):
        p = self.crm("todo", "add", *args)
        return self.scalar("SELECT max(id) FROM todos"), p

    def issue(self, tid):
        return self.linear.issues[self.todo(tid)["linear_id"]]

    def test_offline_by_default(self):
        self.env["LINEAR_API_KEY"] = ""
        tid, _ = self.add("Plain todo", "--person", "Carol Chen", "--due", "tomorrow")
        self.out("todo", "done", str(tid))
        self.out("todo", "edit", str(tid), "--text", "Still plain")
        self.assertIn("Still plain", self.out("todo", "--all"))
        self.assertNotIn("linear", self.out("status").lower())
        self.assertEqual(self.linear.requests, [])
        self.assertIn("Linear: not set up; run: python3 crm.py todo linear setup --team KEY",
                      self.fails("todo", "sync"))
        self.assertEqual(self.linear.requests, [])

    def test_setup(self):
        out = self.setup_linear("--project", "personal")
        self.assertIn("Linear set up: new issues go to Engineering (ENG), project Personal, assigned to Test User.", out)
        cfg = dict(self.query("SELECT key, value FROM config"))
        self.assertEqual(cfg, {"linear.team_id": "team-1", "linear.team_key": "ENG", "linear.viewer_id": "me",
                               "linear.done_state": "st-done", "linear.open_state": "st-todo",
                               "linear.project_id": "proj-1", "linear.project_name": "Personal"})
        self.assertIn("Linear: no team with key NOPE", self.fails("todo", "linear", "setup", "--team", "nope"))
        self.assertIn("no project named 'Elsewhere' in team ENG", self.fails("todo", "linear", "setup", "--team", "ENG",
                                                                             "--project", "Elsewhere"))
        self.setup_linear("--push-all")
        self.assertEqual(self.scalar("SELECT value FROM config WHERE key='linear.push_all'"), "1")
        self.assertIsNone(self.scalar("SELECT value FROM config WHERE key='linear.project_id'"))
        self.assertIn("Linear sync is off", self.out("todo", "linear", "off"))
        self.assertEqual(self.scalar("SELECT count(*) FROM config"), 0)

    def test_push(self):
        self.setup_linear("--project", "Personal")
        tid, p = self.add("Send Carol the deck", "--person", "Carol Chen", "--due", "2030-01-15", "--linear")
        self.assertEqual(p.stderr, "")
        self.assertIn(f"Added #{tid} [ENG-1] Send Carol the deck  (due 2030-01-15", p.stdout)
        i = self.issue(tid)
        self.assertEqual((i["title"], i["dueDate"], i["assigneeId"], i["teamId"], i["projectId"], i["description"]),
                         ("Send Carol the deck", "2030-01-15", "me", "team-1", "proj-1", None))
        self.assertEqual(self.todo(tid)["linear_key"], "ENG-1")
        self.assertIn(f"#{tid} [ENG-1] Send Carol the deck", self.out("todo"))
        self.assertIn(f"todo #{tid} [ENG-1] Send Carol the deck", self.out("person", "Carol Chen"))

        self.out("todo", "done", str(tid))
        self.assertEqual(i["stateId"], "st-done")
        self.out("todo", "undo", str(tid))
        self.assertEqual(i["stateId"], "st-todo")
        self.linear.change(i["id"], stateId="st-doing")  # a custom state survives an edit of the text
        self.out("todo", "edit", str(tid), "--text", "Send Carol the final deck", "--due", "none")
        self.assertEqual((i["title"], i["dueDate"], i["stateId"]), ("Send Carol the final deck", None, "st-doing"))
        self.out("todo", "edit", str(tid), "--person", "Frank Foster")  # local only; nothing to send
        self.assertEqual(self.linear.ops().count("KithUpdate"), 3)

        local, _ = self.add("Local only")
        self.assertIsNone(self.todo(local)["linear_id"])
        self.assertIn(f"Removed #{tid} [ENG-1]", self.out("todo", "rm", str(tid)))
        self.assertIn(i["id"], self.linear.issues)  # rm leaves the issue alone
        self.assertEqual(self.linear.ops().count("KithCreate"), 1)

    def test_person_name_stays_local_unless_shared(self):
        self.setup_linear()
        self.add("Call about the lease", "--person", "Carol Chen", "--linear")
        self.out("todo", "sync", "--import-assigned")
        self.assertFalse(any("Carol" in raw for _, _, raw in self.linear.requests))
        tid, _ = self.add("Call about the lease", "--person", "Carol Chen", "--linear", "--share-person")
        self.assertEqual(self.issue(tid)["description"], "About: Carol Chen")

    def test_push_all(self):
        self.setup_linear("--push-all")
        tid, _ = self.add("Automatically pushed")
        self.assertIsNotNone(self.todo(tid)["linear_id"])
        tid, _ = self.add("Kept local", "--no-linear")
        self.assertIsNone(self.todo(tid)["linear_id"])

    def test_pull(self):
        self.setup_linear()
        a, _ = self.add("Renew passport", "--linear", "--due", "2030-01-01")
        b, _ = self.add("Book flights", "--linear")
        c, _ = self.add("Untouched", "--linear")
        self.linear.change(self.todo(a)["linear_id"], title="Renew passport (both)", dueDate="2030-02-01")
        self.linear.change(self.todo(b)["linear_id"], stateId="st-done")
        self.assertIn("Linear sync: 2 pulled.", self.out("todo", "sync"))
        self.assertEqual((self.todo(a)["text"], self.todo(a)["due"], self.todo(a)["done_at"]),
                         ("Renew passport (both)", "2030-02-01", None))
        self.assertIsNotNone(self.todo(b)["done_at"])
        self.assertEqual(self.todo(c)["text"], "Untouched")
        self.assertIn("Linear sync: no changes.", self.out("todo", "sync"))
        self.assertEqual(self.linear.ops()[-1], "KithIssues")  # nothing written back

        self.linear.change(self.todo(b)["linear_id"], stateId="st-cancel")  # canceled stays done
        self.linear.change(self.todo(a)["linear_id"], stateId="st-cancel")
        self.out("todo", "sync")
        self.assertIsNotNone(self.todo(a)["done_at"])
        self.linear.change(self.todo(b)["linear_id"], stateId="st-backlog")  # reopened in Linear
        self.out("todo", "sync")
        self.assertIsNone(self.todo(b)["done_at"])
        status = self.out("status")
        self.assertIn("linear: last success", status)

    def offline_edit(self, tid, text):
        self.linear.fail = 503
        p = self.crm("todo", "edit", str(tid), "--text", text)
        self.linear.fail = None
        return p

    def test_conflict_local_newer_wins(self):
        self.setup_linear()
        tid, _ = self.add("Original", "--linear")
        iid = self.todo(tid)["linear_id"]
        self.linear.change(iid, title="Changed in Linear first", updated_at="2001-01-01T00:00:00.000Z")
        self.offline_edit(tid, "Changed here later")
        self.assertIn("Linear sync: 1 pushed.", self.out("todo", "sync"))
        self.assertEqual(self.linear.issues[iid]["title"], "Changed here later")
        self.assertEqual(self.todo(tid)["text"], "Changed here later")
        self.assertIn("no changes", self.out("todo", "sync"))

    def test_conflict_remote_newer_wins(self):
        self.setup_linear()
        tid, _ = self.add("Original", "--linear")
        iid = self.todo(tid)["linear_id"]
        self.offline_edit(tid, "Changed here first")
        time.sleep(0.01)
        self.linear.change(iid, title="Changed in Linear later", dueDate="2030-05-05")
        self.assertIn("Linear sync: 1 pulled.", self.out("todo", "sync"))
        self.assertEqual((self.todo(tid)["text"], self.todo(tid)["due"]), ("Changed in Linear later", "2030-05-05"))
        self.assertEqual(self.linear.issues[iid]["title"], "Changed in Linear later")

    def test_import_assigned_dedupes(self):
        self.setup_linear()
        mine, _ = self.add("Already linked", "--linear")
        self.linear.add_issue("Assigned to me", due="2030-03-03")
        self.linear.add_issue("Someone else's", assignee="other")
        self.linear.add_issue("Other team", team="team-2")
        self.linear.add_issue("Already done", state="st-done")
        self.assertNotIn("imported", self.out("todo", "sync"))
        self.assertIn("Linear sync: 1 imported.", self.out("todo", "sync", "--import-assigned"))
        self.assertIn("no changes", self.out("todo", "sync", "--import-assigned"))
        rows = self.query("SELECT text, due, linear_key, person_id FROM todos ORDER BY id")
        self.assertEqual([tuple(r) for r in rows], [("Already linked", None, "ENG-1", None),
                                                    ("Assigned to me", "2030-03-03", "ENG-2", None)])
        imported = self.scalar("SELECT max(id) FROM todos")
        self.out("todo", "done", str(imported))
        self.assertEqual(self.linear.issues["issue-2"]["stateId"], "st-done")

    def test_api_errors_never_break_local_commands(self):
        self.setup_linear()
        self.linear.fail = 500
        tid, p = self.add("Pushed later", "--linear", "--due", "2030-01-01")
        self.assertEqual(p.stderr, "Linear: HTTP 500: Internal error\nwith detail. Saved locally; `todo sync` will retry.\n")
        self.assertIn(f"#{tid} [Linear pending] Pushed later", self.out("todo"))
        self.out("todo", "done", str(tid))
        self.assertIn("Linear: HTTP 500", self.fails("todo", "sync"))
        self.linear.fail = None
        self.assertIn("Linear sync: 1 created.", self.out("todo", "sync"))
        i = self.issue(tid)
        self.assertEqual((i["title"], i["dueDate"], i["stateId"]), ("Pushed later", "2030-01-01", "st-done"))
        self.assertIn("no changes", self.out("todo", "sync"))

        self.env["CRM_LINEAR_URL"] = "http://127.0.0.1:9/graphql"  # nothing listens there
        p = self.crm("todo", "undo", str(tid))
        self.assertIn("Linear: can't reach Linear", p.stderr)
        self.assertEqual(len(p.stderr.strip().splitlines()), 1)
        self.assertIsNone(self.todo(tid)["done_at"])

    def test_bad_or_missing_key(self):
        self.setup_linear()
        self.env["LINEAR_API_KEY"] = "lin_api_wrong"
        tid, p = self.add("Wrong key", "--linear")
        self.assertEqual(p.stderr, "Linear: Linear rejected the API key (HTTP 401). Saved locally; `todo sync` will retry.\n")
        self.env["LINEAR_API_KEY"] = ""
        before = len(self.linear.requests)
        tid2, p = self.add("No key", "--linear")
        self.assertIn("Linear: no API key (set LINEAR_API_KEY", p.stderr)
        self.assertEqual(len(self.linear.requests), before)
        self.assertEqual(self.scalar("SELECT count(*) FROM todos WHERE linear_push=1 AND linear_id IS NULL"), 2)
        self.assertIn("no API key", self.fails("todo", "linear", "setup", "--team", "ENG"))

    def test_add_linear_before_setup(self):
        tid, p = self.add("Waiting for setup", "--linear")
        self.assertIn("Linear isn't set up", p.stderr)
        self.assertEqual(self.linear.requests, [])
        self.setup_linear()
        self.assertIn("1 created", self.out("todo", "sync"))
        self.assertEqual(self.issue(tid)["title"], "Waiting for setup")

    def test_key_is_never_stored(self):
        self.setup_linear()
        self.add("x", "--linear")
        self.linear.fail = 500
        self.crm("todo", "sync", ok=False)
        with open(self.db_path, "rb") as f:
            self.assertNotIn(KEY.encode(), f.read())
        self.assertTrue(all(r[2] != KEY for r in self.linear.requests))

    def test_doctor(self):
        self.env["LINEAR_API_KEY"] = ""
        out = self.crm("doctor", ok=False).stdout
        self.assertIn("linear (optional, to-dos):\n  --    not set up;", out)
        self.env["LINEAR_API_KEY"] = KEY
        self.assertIn("API key found; connect a team: python3 crm.py todo linear setup --team KEY",
                      self.crm("doctor", ok=False).stdout)
        self.setup_linear()
        self.assertIn("  ok    team ENG (Engineering) reachable as Test User", self.crm("doctor", ok=False).stdout)
        self.linear.fail = 500
        self.assertIn("  WARN  can't reach team ENG: HTTP 500", self.crm("doctor", ok=False).stdout)
        self.env["LINEAR_API_KEY"] = ""
        self.assertIn("  WARN  no API key", self.crm("doctor", ok=False).stdout)

    def test_auto_sync_pulls_only_when_set_up_and_stale(self):
        auto = {"CRM_NO_AUTOSYNC": ""}
        self.out("todo", env=auto)
        self.assertEqual(self.linear.requests, [])
        self.setup_linear()
        tid, _ = self.add("Auto", "--linear")
        self.linear.change(self.todo(tid)["linear_id"], title="Auto, renamed in Linear")
        self.assertIn("Auto, renamed in Linear", self.out("todo", env=auto))
        n = len(self.linear.requests)
        self.out("todo", env=auto)  # fresh now: no request
        self.assertEqual(len(self.linear.requests), n)
