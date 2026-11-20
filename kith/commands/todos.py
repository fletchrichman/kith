"""todo: a to-do list in crm.db, each item optionally about a person and optionally due on a date."""
import re, sys
from datetime import date, timedelta

from kith.db import one_person

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
    return f"#{t['id']} {done}{t['text']}" + (f"  ({'; '.join(extra)})" if extra else "")


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
        tid = db.execute("INSERT INTO todos (text, person_id, due) VALUES (?,?,?)",
                         (a.text, pid, parse_due(a.due) if a.due else None)).lastrowid
        db.commit()
        print(f"Added {todo_line(get_todo(db, tid))}")
    elif action in ("done", "undo"):
        t = get_todo(db, a.id)
        if bool(t["done_at"]) == (action == "done"):
            sys.exit(f"#{a.id} is {'already done' if t['done_at'] else 'not done'}.")
        db.execute("UPDATE todos SET done_at = CASE WHEN ? THEN datetime('now') END WHERE id=?", (action == "done", a.id))
        db.commit()
        print(("Done: " if action == "done" else "Reopened: ") + todo_line(get_todo(db, a.id)))
    elif action == "rm":
        t = get_todo(db, a.id)
        db.execute("DELETE FROM todos WHERE id=?", (a.id,))
        db.commit()
        print(f"Removed {todo_line(t)}")
    elif action == "edit":
        get_todo(db, a.id)
        if a.text is None and a.due is None and a.person is None:
            sys.exit("Nothing to change: give --text, --due or --person.")
        if a.text is not None:
            db.execute("UPDATE todos SET text=? WHERE id=?", (a.text, a.id))
        if a.due is not None:
            db.execute("UPDATE todos SET due=? WHERE id=?", (parse_due(a.due), a.id))
        if a.person is not None:
            pid = None if a.person.strip().lower() in ("", "none") else one_person(db, a.person)["id"]
            db.execute("UPDATE todos SET person_id=? WHERE id=?", (pid, a.id))
        db.commit()
        print(f"Updated {todo_line(get_todo(db, a.id))}")
    else:
        list_todos(db, a)


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
