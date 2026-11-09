"""Render the packaged view and publish reports to a local data directory."""

import fcntl
import json
import os
from importlib.resources import files
from pathlib import Path
import tempfile

from .common import parse_time
from .planner import plan

DOCUMENT_HEAD = (
    '<!doctype html><html lang="en"><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    '<meta http-equiv="refresh" content="60"><title>Next from Linear</title>'
    "<style>:root{color-scheme:light}body{margin:0;padding:0;min-width:0}"
    "button,input,select{font:inherit}[hidden]{display:none!important}</style>"
)


def render(report):
    """Render inert issue text into the bundled, project-grouped UI."""
    template = files(__package__).joinpath("view.html").read_text(encoding="utf-8")
    data = json.dumps(report)
    for char, escaped in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026")):
        data = data.replace(char, escaped)
    return template.replace("/*QUEUE_DATA*/null", data)


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix="." + path.name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def publish(report, directory, inline=None):
    """Serialize publishers and reject reports older than the published snapshot.

    The lock coordinates publication only, never work claims in Linear.
    """
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    report = dict(report, queueDirectory=str(directory))
    with (directory / ".publish.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        existing = directory / "queue.json"
        if existing.exists():
            published = json.loads(existing.read_text(encoding="utf-8"))
            if parse_time(published["capturedAt"]) > parse_time(report["capturedAt"]):
                raise ValueError("A newer snapshot is already published")
        page = render(report)
        atomic_write(directory / "queue.json", json.dumps(report, indent=2))
        atomic_write(directory / "widget.html", DOCUMENT_HEAD + page + "</html>")
        if inline:
            atomic_write(inline, page)
    return report


def build(snapshot_path, review_path, directory, *, limit=3, inline=None):
    snapshot = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
    review = json.loads(Path(review_path).read_text(encoding="utf-8"))
    return publish(plan(snapshot, review, limit), directory, inline)
