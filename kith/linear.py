"""Opt-in Linear sync for todos. crm.db stays the source of truth: nothing here runs until
`todo linear setup`, and a Linear failure never undoes a local change. The API key comes from
LINEAR_API_KEY or the macOS Keychain and is never written to crm.db or printed."""
import json, os, subprocess, sys, urllib.error, urllib.request

from kith.db import log_sync

API_URL = os.environ.get("CRM_LINEAR_URL", "https://api.linear.app/graphql")
KEYCHAIN_SERVICE = "kith-linear"
NOW = "strftime('%Y-%m-%d %H:%M:%f', 'now')"
ISSUE = "id identifier title dueDate updatedAt completedAt canceledAt state { type }"
DONE_TYPES = ("completed", "canceled")


class LinearError(Exception):
    pass


def api_key():
    """LINEAR_API_KEY if set (empty means none), else the Keychain item "kith-linear" on macOS."""
    if "LINEAR_API_KEY" in os.environ:
        return os.environ["LINEAR_API_KEY"].strip() or None
    if sys.platform != "darwin":
        return None
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return (r.stdout.strip() or None) if r.returncode == 0 else None


def gql(query, variables=None):
    key = api_key()
    if not key:
        raise LinearError("no API key (set LINEAR_API_KEY or add it to the Keychain; see SETUP.md)")
    req = urllib.request.Request(API_URL, data=json.dumps({"query": query, "variables": variables or {}}).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": key})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise LinearError(f"Linear rejected the API key (HTTP {e.code})") from None
        try:
            msg = json.load(e)["errors"][0]["message"]
        except Exception:
            msg = e.reason
        raise LinearError(f"HTTP {e.code}: {msg}") from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise LinearError(f"can't reach Linear: {getattr(e, 'reason', e)}") from None
    if body.get("errors"):
        raise LinearError(str(body["errors"][0].get("message", "unknown error")))
    return body["data"]


def ts(v):
    """Linear's 2026-11-20T16:43:50.243Z as 2026-11-20 16:43:50.243, comparable with NOW."""
    if not v:
        return None
    s = v.replace("T", " ").rstrip("Z")
    return (s if "." in s else s + ".000")[:23]


def config(db):
    return {r[0][7:]: r[1] for r in db.execute("SELECT key, value FROM config WHERE key LIKE 'linear.%'")}


def setup(db, team_key, project=None, push_all=None):
    d = gql("""query KithSetup($key: String!) { viewer { id name }
        teams(filter: {key: {eq: $key}}) { nodes { id key name states { nodes { id name type position } } } } }""",
            {"key": team_key.upper()})
    if not d["teams"]["nodes"]:
        raise LinearError(f"no team with key {team_key.upper()} visible to this API key")
    team = d["teams"]["nodes"][0]
    states = sorted(team["states"]["nodes"], key=lambda s: s["position"])
    pick = lambda *types: next((s["id"] for t in types for s in states if s["type"] == t), None)
    cfg = {"team_id": team["id"], "team_key": team["key"], "viewer_id": d["viewer"]["id"],
           "done_state": pick("completed"), "open_state": pick("unstarted", "backlog", "started"),
           "project_id": None, "project_name": None}
    if not cfg["done_state"] or not cfg["open_state"]:
        raise LinearError(f"team {team['key']} has no completed or open workflow state")
    if project:
        ps = gql("""query KithProject($name: String!) { projects(filter: {name: {eqIgnoreCase: $name}}) {
            nodes { id name teams { nodes { id } } } } }""", {"name": project})["projects"]["nodes"]
        ps = [p for p in ps if any(t["id"] == team["id"] for t in p["teams"]["nodes"])]
        if not ps:
            raise LinearError(f"no project named '{project}' in team {team['key']}")
        cfg.update(project_id=ps[0]["id"], project_name=ps[0]["name"])
    if push_all is not None:
        cfg["push_all"] = "1" if push_all else "0"
    for k, v in cfg.items():
        db.execute("INSERT OR REPLACE INTO config VALUES (?, ?)", ("linear." + k, v))
    db.commit()
    return cfg, d["viewer"]["name"], team["name"]


def off(db):
    db.execute("DELETE FROM config WHERE key LIKE 'linear.%'")
    db.commit()


def saved(db, tid, issue):
    db.execute(f"""UPDATE todos SET linear_id=?, linear_key=?, linear_updated_at=?, linear_push=0,
        linear_synced_at={NOW} WHERE id=?""", (issue["id"], issue["identifier"], ts(issue["updatedAt"]), tid))
    db.commit()


def create(db, cfg, t):
    inp = {"teamId": cfg["team_id"], "assigneeId": cfg["viewer_id"], "title": t["text"], "dueDate": t["due"]}
    if cfg.get("project_id"):
        inp["projectId"] = cfg["project_id"]
    if t["done_at"]:  # done while it waited to be pushed
        inp["stateId"] = cfg["done_state"]
    if t["share_person"] and t["person_id"]:
        name = db.execute("SELECT name FROM people WHERE id=?", (t["person_id"],)).fetchone()[0]
        inp["description"] = f"About: {name}"
    issue = gql(f"""mutation KithCreate($input: IssueCreateInput!) {{ issueCreate(input: $input) {{
        success issue {{ {ISSUE} }} }} }}""", {"input": inp})["issueCreate"]["issue"]
    saved(db, t["id"], issue)
    return issue


def update(db, cfg, t, fields):
    """Set title, dueDate and/or done (mapped to the team's done or open state) on the linked issue."""
    inp = {k: v for k, v in fields.items() if k != "done"}
    if "done" in fields:
        inp["stateId"] = cfg["done_state"] if fields["done"] else cfg["open_state"]
    issue = gql(f"""mutation KithUpdate($id: String!, $input: IssueUpdateInput!) {{ issueUpdate(id: $id, input: $input) {{
        success issue {{ {ISSUE} }} }} }}""", {"id": t["linear_id"], "input": inp})["issueUpdate"]["issue"]
    saved(db, t["id"], issue)
    return issue


def push_change(db, tid, fields):
    """After a local change: create or update the linked issue. Problems are one line on stderr."""
    cfg = config(db)
    t = db.execute("SELECT * FROM todos WHERE id=?", (tid,)).fetchone()
    if not t or not (t["linear_id"] or t["linear_push"]):
        return
    if not cfg.get("team_id"):
        print("Linear isn't set up (todo linear setup --team KEY); saved locally only.", file=sys.stderr)
        return
    try:
        if t["linear_id"]:
            update(db, cfg, t, fields)
        else:
            create(db, cfg, t)
    except LinearError as e:
        print(f"Linear: {e}. Saved locally; `todo sync` will retry.", file=sys.stderr)


def pull(db, t, issue):
    done = issue["state"]["type"] in DONE_TYPES
    done_at = (t["done_at"] or (ts(issue["completedAt"] or issue["canceledAt"]) or "")[:19] or None) if done else None
    db.execute(f"""UPDATE todos SET text=?, due=?, done_at=coalesce(?, CASE WHEN ? THEN datetime('now') END),
        linear_key=?, linear_updated_at=?, updated_at={NOW}, linear_synced_at={NOW} WHERE id=?""",
               (issue["title"], issue["dueDate"], done_at, done, issue["identifier"], ts(issue["updatedAt"]), t["id"]))
    db.commit()


def sync(db, import_assigned=False):
    """Create pending issues, reconcile linked todos (newer change wins), optionally import my issues."""
    cfg = config(db)
    if not cfg.get("team_id"):
        raise LinearError("not set up; run: python3 crm.py todo linear setup --team KEY")
    n = dict(created=0, pushed=0, pulled=0, imported=0, missing=0)
    for t in db.execute("SELECT * FROM todos WHERE linear_push=1 AND linear_id IS NULL").fetchall():
        create(db, cfg, t)
        n["created"] += 1
    linked = db.execute("SELECT * FROM todos WHERE linear_id IS NOT NULL").fetchall()
    remote = {}
    for i in range(0, len(linked), 50):
        ids = [t["linear_id"] for t in linked[i:i + 50]]
        for issue in gql(f"""query KithIssues($ids: [ID!]) {{ issues(filter: {{id: {{in: $ids}}}}, first: 100) {{
                nodes {{ {ISSUE} }} }} }}""", {"ids": ids})["issues"]["nodes"]:
            remote[issue["id"]] = issue
    for t in linked:
        issue = remote.get(t["linear_id"])
        if not issue:
            n["missing"] += 1
            continue
        remote_changed = ts(issue["updatedAt"]) != t["linear_updated_at"]
        local_changed = t["updated_at"] > (t["linear_synced_at"] or "")
        if remote_changed and (not local_changed or ts(issue["updatedAt"]) > t["updated_at"]):
            pull(db, t, issue)
            n["pulled"] += 1
        elif local_changed:
            fields = {k: v for k, v, theirs in (("title", t["text"], issue["title"]), ("dueDate", t["due"], issue["dueDate"]),
                                                ("done", bool(t["done_at"]), issue["state"]["type"] in DONE_TYPES))
                      if v != theirs}
            if fields:
                update(db, cfg, t, fields)
                n["pushed"] += 1
            else:
                saved(db, t["id"], issue)
    if import_assigned:
        issues = gql(f"""query KithAssigned($team: ID!) {{ viewer {{ assignedIssues(first: 100, filter: {{
            team: {{id: {{eq: $team}}}}, state: {{type: {{nin: ["completed", "canceled"]}}}}}}) {{ nodes {{ {ISSUE} }} }} }} }}""",
                     {"team": cfg["team_id"]})["viewer"]["assignedIssues"]["nodes"]
        for issue in issues:
            if db.execute("SELECT 1 FROM todos WHERE linear_id=?", (issue["id"],)).fetchone():
                continue
            db.execute(f"""INSERT INTO todos (text, due, linear_id, linear_key, linear_updated_at, linear_synced_at)
                VALUES (?, ?, ?, ?, ?, {NOW})""", (issue["title"], issue["dueDate"], issue["id"], issue["identifier"],
                                                 ts(issue["updatedAt"])))
            n["imported"] += 1
        db.commit()
    detail = ", ".join(f"{v} {k}" for k, v in n.items() if v) or "no changes"
    log_sync(db, "linear", detail)
    db.commit()
    return detail


def check(db):
    """For doctor: which team and user the key reaches. Raises LinearError."""
    cfg = config(db)
    d = gql("query KithCheck($team: String!) { viewer { name } team(id: $team) { key name } }", {"team": cfg["team_id"]})
    return f"team {d['team']['key']} ({d['team']['name']}) reachable as {d['viewer']['name']}"
