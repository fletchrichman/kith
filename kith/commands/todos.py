"""todo: a to-do list in crm.db, each item optionally about a person and optionally due on a date."""
import re, sys
from datetime import date, timedelta

from kith import linear
from kith.db import one_person
from kith.linear import NOW, LinearError

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def parse_due(s, today=None):
    """YYYY-MM-DD, today, tomorrow, a weekday (its next occurrence after today), +3d or +2w.
    Returns an ISO date string, or None for "none"/"" (no due date)."""
    today = today or date.today()
    s = s.strip().lower()
    if s in ("", "none"):
        return None
    if s == "today":
        return today.isoformat()
    if s == "tomorrow":
        return (today + timedelta(days=1)).isoformat()
    for i, name in enumerate(WEEKDAYS):
        if len(s) >= 3 and name.startswith(s):
            return (today + timedelta(days=(i - today.weekday() - 1) % 7 + 1)).isoformat()
    m = re.fullmatch(r"\+(\d+)([dw])", s)
    if m:
        return (today + timedelta(days=int(m[1]) * (7 if m[2] == "w" else 1))).isoformat()
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError:
        sys.exit(f"Can't read due date '{s}'. Use YYYY-MM-DD, today, tomorrow, a weekday, +3d or +2w.")


def due_label(due, today=None):
    if not due:
        return ""
    d = (date.fromisoformat(due) - (today or date.today())).days
    if d < 0:
        return f"due {due} ({-d}d overdue)"
    if d == 0:
        return f"due {due} (today)"
    if d == 1:
        return f"due {due} (tomorrow)"
    return f"due {due} ({date.fromisoformat(due).strftime('%a')}, in {d}d)"


def todo_line(t, show_person=True):
    """One todo as text; t needs id, text, due, done_at, person_id and person (the name)."""
    extra = [due_label(t["due"])] if t["due"] and not t["done_at"] else []
    if show_person and t["person_id"]:
        extra.append(f"{t['person']} [{t['person_id']}]")
    done = f"done {t['done_at'][:10]}: " if t["done_at"] else ""
    issue = f"[{t['linear_key']}] " if t["linear_key"] else "[Linear pending] " if t["linear_push"] else ""
    return f"#{t['id']} {issue}{done}{t['text']}" + (f"  ({'; '.join(extra)})" if extra else "")


def open_todos(db, person_id=None):
    """Open todos, soonest due first (so overdue ones lead), then undated ones oldest first."""
    return db.execute("""SELECT t.*, p.name AS person FROM todos t LEFT JOIN people p ON p.id = t.person_id
        WHERE t.done_at IS NULL AND (? IS NULL OR t.person_id = ?)
        ORDER BY t.due IS NULL, t.due, t.id""", (person_id, person_id)).fetchall()


def get_todo(db, todo_id):
    t = db.execute("""SELECT t.*, p.name AS person FROM todos t LEFT JOIN people p ON p.id = t.person_id
        WHERE t.id=?""", (todo_id,)).fetchone()
    if not t:
        sys.exit(f"No todo #{todo_id}.")
    return t


def cmd_todo(db, a):
    action = getattr(a, "action", None)
    if action == "add":
        pid = one_person(db, a.person)["id"] if a.person else None
        push = a.linear if a.linear is not None else linear.config(db).get("push_all") == "1"
        tid = db.execute("INSERT INTO todos (text, person_id, due, linear_push, share_person) VALUES (?,?,?,?,?)",
                         (a.text, pid, parse_due(a.due) if a.due else None, push, a.share_person)).lastrowid
        db.commit()
        linear.push_change(db, tid, {})
        print(f"Added {todo_line(get_todo(db, tid))}")
    elif action in ("done", "undo"):
        t = get_todo(db, a.id)
        if bool(t["done_at"]) == (action == "done"):
            sys.exit(f"#{a.id} is {'already done' if t['done_at'] else 'not done'}.")
        db.execute(f"UPDATE todos SET done_at = CASE WHEN ? THEN datetime('now') END, updated_at={NOW} WHERE id=?",
                   (action == "done", a.id))
        db.commit()
        linear.push_change(db, a.id, {"done": action == "done"})
        print(("Done: " if action == "done" else "Reopened: ") + todo_line(get_todo(db, a.id)))
    elif action == "rm":
        t = get_todo(db, a.id)
        db.execute("DELETE FROM todos WHERE id=?", (a.id,))
        db.commit()
        print(f"Removed {todo_line(t)}" + (f"\nLinear issue {t['linear_key']} is left as it is." if t["linear_key"] else ""))
    elif action == "edit":
        get_todo(db, a.id)
        if a.text is None and a.due is None and a.person is None:
            sys.exit("Nothing to change: give --text, --due or --person.")
        fields = {}
        if a.text is not None:
            db.execute("UPDATE todos SET text=? WHERE id=?", (a.text, a.id))
            fields["title"] = a.text
        if a.due is not None:
            fields["dueDate"] = parse_due(a.due)
            db.execute("UPDATE todos SET due=? WHERE id=?", (fields["dueDate"], a.id))
        if a.person is not None:
            pid = None if a.person.strip().lower() in ("", "none") else one_person(db, a.person)["id"]
            db.execute("UPDATE todos SET person_id=? WHERE id=?", (pid, a.id))
        db.execute(f"UPDATE todos SET updated_at={NOW} WHERE id=?", (a.id,))
        db.commit()
        if fields:
            linear.push_change(db, a.id, fields)
        print(f"Updated {todo_line(get_todo(db, a.id))}")
    elif action == "sync":
        try:
            print(f"Linear sync: {linear.sync(db, a.import_assigned)}.")
        except LinearError as e:
            sys.exit(f"Linear: {e}")
    elif action == "linear":
        cmd_linear(db, a)
    else:
        list_todos(db, a)


def cmd_linear(db, a):
    if a.linear_action == "off":
        linear.off(db)
        print("Linear sync is off. Linked todos keep their issue ids; `todo linear setup` turns it back on.")
        return
    try:
        cfg, me, team = linear.setup(db, a.team, a.project, a.push_all)
    except LinearError as e:
        sys.exit(f"Linear: {e}")
    print(f"Linear set up: new issues go to {team} ({cfg['team_key']})" +
          (f", project {cfg['project_name']}" if cfg["project_name"] else "") + f", assigned to {me}.")
    print("Push a todo with `todo add ... --linear`" +
          (" (every new todo is pushed: --push-all is on)." if linear.config(db).get("push_all") == "1" else "."))


def list_todos(db, a):
    pid = one_person(db, a.list_person)["id"] if a.list_person else None
    rows = [] if a.done else open_todos(db, pid)
    if a.done or a.all:
        rows += db.execute("""SELECT t.*, p.name AS person FROM todos t LEFT JOIN people p ON p.id = t.person_id
            WHERE t.done_at IS NOT NULL AND (? IS NULL OR t.person_id = ?)
            ORDER BY t.done_at DESC, t.id DESC""", (pid, pid)).fetchall()
    for t in rows:
        print(todo_line(t))
    if not rows:
        print("No done todos." if a.done else "No open todos.")
