"""A tiny stand-in for Linear's GraphQL API, served on localhost so tests never touch the network.
It answers the named operations kith/linear.py sends (KithSetup, KithCreate, ...) and records every
request so tests can check exactly what left the machine."""
import json
import re
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KEY = "lin_api_test_key"
TEAM = {"id": "team-1", "key": "ENG", "name": "Engineering"}
STATES = [{"id": "st-backlog", "name": "Backlog", "type": "backlog", "position": 0},
          {"id": "st-todo", "name": "Todo", "type": "unstarted", "position": 1},
          {"id": "st-doing", "name": "In Progress", "type": "started", "position": 2},
          {"id": "st-done", "name": "Done", "type": "completed", "position": 3},
          {"id": "st-cancel", "name": "Canceled", "type": "canceled", "position": 4}]
STATE_TYPE = {s["id"]: s["type"] for s in STATES}


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:23] + "Z"


class FakeLinear:
    def __init__(self):
        self.issues = {}  # id -> stored issue
        self.requests = []  # (operation, variables, raw body)
        self.fail = None  # an HTTP status to answer every request with
        self.lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"])).decode()
                body = json.loads(raw)
                op = re.search(r"(?:query|mutation) (\w+)", body["query"]).group(1)
                with fake.lock:
                    fake.requests.append((op, body["variables"], raw))
                    if self.headers.get("Authorization") != KEY:
                        return self.reply(401, {"errors": [{"message": "Authentication required"}]})
                    if fake.fail:
                        return self.reply(fake.fail, {"errors": [{"message": "Internal error\nwith detail"}]})
                    self.reply(200, {"data": getattr(fake, op)(body["variables"])})

            def reply(self, code, payload):
                data = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/graphql"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def ops(self):
        return [r[0] for r in self.requests]

    # Test helpers.
    def add_issue(self, title, assignee="me", team="team-1", state="st-todo", due=None):
        n = len(self.issues) + 1
        i = {"id": f"issue-{n}", "identifier": f"ENG-{n}", "title": title, "dueDate": due, "description": None,
             "assigneeId": assignee, "teamId": team, "projectId": None, "stateId": state,
             "updatedAt": now(), "completedAt": None}
        self.issues[i["id"]] = i
        return i

    def change(self, issue_id, updated_at=None, **fields):
        """Edit an issue as if in Linear's app; updated_at overrides the new updatedAt."""
        i = self.issues[issue_id]
        i.update(fields)
        i["completedAt"] = now() if STATE_TYPE[i["stateId"]] == "completed" else None
        i["updatedAt"] = updated_at or now()

    def view(self, i):
        t = STATE_TYPE[i["stateId"]]
        return {"id": i["id"], "identifier": i["identifier"], "title": i["title"], "dueDate": i["dueDate"],
                "updatedAt": i["updatedAt"], "completedAt": i["completedAt"],
                "canceledAt": now() if t == "canceled" else None, "state": {"type": t}}

    # GraphQL operations.
    def KithSetup(self, v):
        teams = [dict(TEAM, states={"nodes": STATES})] if v["key"] == TEAM["key"] else []
        return {"viewer": {"id": "me", "name": "Test User"}, "teams": {"nodes": teams}}

    def KithProject(self, v):
        projects = [{"id": "proj-1", "name": "Personal", "teams": {"nodes": [{"id": "team-1"}]}},
                    {"id": "proj-2", "name": "Elsewhere", "teams": {"nodes": [{"id": "team-2"}]}}]
        return {"projects": {"nodes": [p for p in projects if p["name"].lower() == v["name"].lower()]}}

    def KithCheck(self, v):
        return {"viewer": {"name": "Test User"}, "team": {"key": TEAM["key"], "name": TEAM["name"]}}

    def KithCreate(self, v):
        inp = v["input"]
        i = self.add_issue(inp["title"], assignee=inp.get("assigneeId"), team=inp["teamId"],
                           state=inp.get("stateId", "st-todo"), due=inp.get("dueDate"))
        i.update(description=inp.get("description"), projectId=inp.get("projectId"))
        return {"issueCreate": {"success": True, "issue": self.view(i)}}

    def KithUpdate(self, v):
        inp = v["input"]
        self.change(v["id"], **{k: inp[k] for k in ("title", "dueDate", "stateId") if k in inp})
        return {"issueUpdate": {"success": True, "issue": self.view(self.issues[v["id"]])}}

    def KithIssues(self, v):
        return {"issues": {"nodes": [self.view(self.issues[i]) for i in v["ids"] if i in self.issues]}}

    def KithAssigned(self, v):
        return {"viewer": {"assignedIssues": {"nodes": [
            self.view(i) for i in self.issues.values() if i["assigneeId"] == "me" and i["teamId"] == v["team"]
            and STATE_TYPE[i["stateId"]] not in ("completed", "canceled")]}}}
