"""sync, and the quiet auto-sync read commands run first."""
import os, sys
from datetime import datetime, timezone, timedelta

from kith.config import AUTO_SYNC_HOURS
from kith.db import log_failure
from kith.sources import SYNCERS
from kith.sources.linkedin import sync_linkedin


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
    failed = []
    for name, fn in SYNCERS.items():
        try:
            fn(db); db.commit()
        except Exception as e:
            failed.append(f"{name} ({log_failure(db, name, e)[:120]})")
    print(f"(auto-synced; data was older than {AUTO_SYNC_HOURS}h" +
          (f"; failed: {'; '.join(failed)}. Run: python3 crm.py doctor" if failed else "") + ")", file=sys.stderr)


def cmd_sync(db, a):
    sources = a.sources or list(SYNCERS)
    bad = [s for s in sources if s not in SYNCERS]
    if bad:
        sys.exit(f"unknown source(s): {', '.join(bad)} (choose from {', '.join(SYNCERS)})")
    failed = False
    for s in sources:
        try:
            print(SYNCERS[s](db))
            db.commit()
        except Exception as e:
            print(f"{s}: FAILED - {log_failure(db, s, e)}")
            failed = True
    if a.linkedin:
        try:
            print(sync_linkedin(db, a.linkedin)); db.commit()
        except Exception as e:
            print(f"linkedin: FAILED - {log_failure(db, 'linkedin', e)}")
            failed = True
    if failed:
        print("Run `python3 crm.py doctor` for what to fix.")
