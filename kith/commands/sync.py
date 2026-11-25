"""sync, and the quiet auto-sync read commands run first."""
import os, sys
from datetime import datetime, timezone, timedelta

from kith.config import AUTO_SYNC_HOURS
from kith.db import log_failure, log_sync
from kith.sources import SYNCERS
from kith.sources.linkedin import sync_linkedin


def sync_one(db, name, fn, quiet=False):
    """Sync one source in its own transaction and log the outcome. Returns the error message, or None."""
    try:
        detail = fn(db)
        log_sync(db, name, detail)
        db.commit()
    except Exception as e:
        err = log_failure(db, name, e)
        if not quiet:
            print(f"{name}: FAILED - {err}")
        return err
    if not quiet:
        print(f"{name}: {detail}")


def auto_sync(db):
    """Refresh quietly if data is stale, so answers are current without a scheduler."""
    if os.environ.get("CRM_NO_AUTOSYNC"):
        return
    # Stale = a core source not attempted (successfully or not) within AUTO_SYNC_HOURS, so a
    # failing source is retried every few hours rather than on every command.
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=AUTO_SYNC_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    fresh = db.execute("""SELECT count(*) FROM sync_log WHERE source IN ('contacts','messages','calendar')
        AND coalesce(last_attempt_at, last_success_at) > ?""", (cutoff,)).fetchone()[0]
    if fresh == 3:
        return
    errors = {name: sync_one(db, name, fn, quiet=True) for name, fn in SYNCERS.items()}
    failed = [f"{name} ({err[:120]})" for name, err in errors.items() if err]
    print(f"(auto-synced; data was older than {AUTO_SYNC_HOURS}h" +
          (f"; failed: {'; '.join(failed)}. Run: python3 crm.py doctor" if failed else "") + ")", file=sys.stderr)


def cmd_sync(db, a):
    sources = a.sources or list(SYNCERS)
    bad = [s for s in sources if s not in SYNCERS]
    if bad:
        sys.exit(f"unknown source(s): {', '.join(bad)} (choose from {', '.join(SYNCERS)})")
    jobs = [(s, SYNCERS[s]) for s in sources]
    if a.linkedin:
        jobs.append(("linkedin", lambda db: sync_linkedin(db, a.linkedin)))
    failed = [name for name, fn in jobs if sync_one(db, name, fn)]
    if failed:
        print("Run `python3 crm.py doctor` for what to fix.")
