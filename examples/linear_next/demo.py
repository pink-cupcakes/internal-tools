"""Build a fresh synthetic two-project demo without querying external services."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from internal_tools.linear_next import digest, plan, publish


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path(".linear-next/demo"))
    options = parser.parse_args()
    source = Path(__file__).resolve().parent
    snapshot = json.loads((source / "snapshot.json").read_text(encoding="utf-8"))
    review = json.loads((source / "review.json").read_text(encoding="utf-8"))
    snapshot["capturedAt"] = datetime.now(timezone.utc).isoformat()
    review["snapshotDigest"] = digest(snapshot)
    options.directory.mkdir(parents=True, exist_ok=True)
    for name, data in (("snapshot", snapshot), ("review", review)):
        (options.directory / f"{name}.json").write_text(
            json.dumps(data, indent=2) + "\n", encoding="utf-8"
        )
    publish(plan(snapshot, review), options.directory)
    print(options.directory.resolve() / "widget.html")


if __name__ == "__main__":
    main()
