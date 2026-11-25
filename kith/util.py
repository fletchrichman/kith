"""Normalizing phones, emails and names, and converting timestamps."""
import re
from datetime import datetime, timezone

from kith.config import APPLE_EPOCH


def norm_phone(raw):
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return None
    if raw.strip().startswith("+") or len(digits) > 10:
        return "+" + digits
    return "+1" + digits if len(digits) == 10 else digits  # assume US/Canada for bare 10-digit numbers


def norm_email(raw):
    return raw.strip().lower() if raw and "@" in raw else None


def norm_handle(raw):
    return norm_email(raw) if "@" in (raw or "") else norm_phone(raw)


def handle_kind(handle):
    return "email" if "@" in handle else "phone"


def norm_name(n):
    return re.sub(r"\s+", " ", (n or "").strip().lower())


def utc(unix):
    """Unix seconds as the "YYYY-MM-DD HH:MM:SS" UTC text crm.db stores."""
    return datetime.fromtimestamp(unix, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def apple_ts(v):
    """Apple timestamps: seconds (or nanoseconds on newer DBs) since 2001."""
    if v is None:
        return None
    v = float(v)
    return utc((v / 1e9 if v > 1e12 else v) + APPLE_EPOCH)


def latest(*ts):
    """The newest of some timestamps, ignoring missing ones (None if all are missing)."""
    return max(filter(None, ts), default=None)


def days_ago(ts):
    if not ts:
        return None
    return (datetime.now(timezone.utc) - datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
            .replace(tzinfo=timezone.utc)).days


def ago(ts):
    d = days_ago(ts)
    return "never" if d is None else f"{ts[:10]} ({d}d ago)"
