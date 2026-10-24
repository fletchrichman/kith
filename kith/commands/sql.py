"""Read-only SQL against crm.db."""
import sqlite3, sys

from kith.config import DB_PATH


def cmd_sql(db, a):
    ro = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    # mode=ro blocks writes to crm.db; ATTACH would still create or open other files.
    ro.set_authorizer(lambda action, *_: sqlite3.SQLITE_DENY if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH)
                      else sqlite3.SQLITE_OK)
    try:
        cur = ro.execute(a.query)
    except (sqlite3.Error, sqlite3.Warning) as e:
        sys.exit(f"sql: {e} (read-only: SELECT, WITH, EXPLAIN and read-only PRAGMAs work)")
    cols = [d[0] for d in cur.description or []]
    print(" | ".join(cols))
    for row in cur.fetchmany(a.limit):
        print(" | ".join("" if v is None else str(v) for v in row))
