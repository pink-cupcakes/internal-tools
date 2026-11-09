"""Stable priority order and separate ownership for each proposed batch."""


def reach(ident, dependencies):
    seen, todo = set(), [ident]
    while todo:
        item = todo.pop()
        for target, deps in dependencies.items():
            if item in deps and target not in seen and target != ident:
                seen.add(target)
                todo.append(target)
    return len(seen)


def rank(row, issues, dependencies):
    issue = issues[row["id"]]
    priority = issue.get("priority", 0)
    if isinstance(priority, dict):
        priority = priority.get("value", 0)
    return (
        priority or 5,
        -reach(row["id"], dependencies),
        issue.get("createdAt", ""),
        row["id"],
    )


def select_batch(statuses, issues, dependencies, conflicts, limit):
    candidates = sorted(
        (row for row in statuses.values() if row["state"] == "ready"),
        key=lambda row: rank(row, issues, dependencies),
    )
    selected = []
    for row in candidates:
        clashes = [
            other["id"]
            for other in selected
            if set(other["resources"]) & set(row["resources"])
            or tuple(sorted((other["id"], row["id"]))) in conflicts
        ]
        if clashes:
            row["state"] = "sequenced"
            row["reasons"].append(
                "Run after this batch; shares ownership with " + ", ".join(clashes)
            )
        elif len(selected) < limit:
            selected.append(row)
        row["unlocks"] = reach(row["id"], dependencies)
    return selected
