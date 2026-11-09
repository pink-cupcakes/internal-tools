"""Snapshot identity, workflow state, and freshness helpers."""

import hashlib
import json
import re
from datetime import datetime, timezone

MAX_AGE_SECONDS = 7200
REQUIRED_COVERAGE = ("issues", "relations", "comments", "diffs", "threads")


def digest(snapshot):
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def utcnow():
    return datetime.now(timezone.utc)


def parse_time(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timestamp must include timezone")
    return result


def active(issue):
    return issue.get("statusType") == "started"


def complete(issue):
    # Canceled is not evidence of a delivered prerequisite.
    return issue.get("statusType") == "completed"


def project_key(issue):
    return issue.get("projectId") or issue.get("teamId") or "workspace"


def tokens(title):
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
