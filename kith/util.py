"""Normalizing phones, emails and names, and converting timestamps."""
import re
from datetime import datetime, timezone

from kith.config import APPLE_EPOCH


def norm_phone(raw):
    if not raw:
        return None
    s = raw.strip()
    digits = re.sub(r"\D", "", s)
    if not digits:
        return None
    if s.startswith("+"):
        return "+" + digits
    if len(digits) == 10:  # assume US/Canada for bare 10-digit numbers
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return "+" + digits if len(digits) > 10 else digits


def norm_email(raw):
    return raw.strip().lower() if raw and "@" in raw else None


def norm_handle(raw):
    return norm_email(raw) if "@" in (raw or "") else norm_phone(raw)


def apple_ts(v):
    """Apple timestamps: seconds (or nanoseconds on newer DBs) since 2001."""
    if v is None:
        return None
    v = float(v)
    if v > 1e12:
        v /= 1e9
    return datetime.fromtimestamp(v + APPLE_EPOCH, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def days_ago(ts):
    if not ts:
        return None
    return (datetime.now(timezone.utc) - datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
            .replace(tzinfo=timezone.utc)).days


def ago(ts):
    d = days_ago(ts)
    return "never" if d is None else f"{ts[:10]} ({d}d ago)"


def norm_name(n):
    return re.sub(r"\s+", " ", (n or "").strip().lower())
