"""Declared dependencies and independently reviewed outcome relationships."""

from difflib import SequenceMatcher

from .common import project_key, tokens


def declared_relations(issues, gaps):
    dependencies = {key: set() for key in issues}
    duplicates = set()
    for ident, issue in issues.items():
        relations = issue.get("relations")
        if relations is None:
            gaps.append("relations:" + ident)
            relations = {}
        dependencies[ident].update(b["id"] for b in relations.get("blockedBy", []))
        for blocked in relations.get("blocks", []):
            if blocked["id"] in dependencies:
                dependencies[blocked["id"]].add(ident)
        duplicate = relations.get("duplicateOf")
        if duplicate:
            target = duplicate["id"] if isinstance(duplicate, dict) else duplicate
            duplicates.add(tuple(sorted((ident, target))))
    return dependencies, duplicates


def apply_edge(edge, dependencies, duplicates, conflicts):
    a, b, kind = edge["from"], edge["to"], edge["kind"]
    if not edge.get("evidence"):
        raise ValueError("Every inferred relation requires evidence")
    if kind == "requires":
        dependencies[a].add(b)
    elif kind == "satisfied_gate":
        if not edge.get("artifact"):
            raise ValueError("Discharging a gate requires an artifact reference")
        dependencies[a].discard(b)
    elif kind in ("overlap", "possible_duplicate"):
        pair = tuple(sorted((a, b)))
        conflicts.add(pair)
        if kind == "possible_duplicate":
            duplicates.add(pair)
    else:
        raise ValueError("Unknown inferred relation kind: " + kind)


def reviewed_relations(issues, edges, dependencies, duplicates, gaps, warnings):
    conflicts = set()
    for edge in edges:
        a, b = edge["from"], edge["to"]
        if a not in issues or b not in issues:
            warnings.append("Unresolved inferred relation: " + a + " / " + b)
            gaps.append("inferred relation target")
            continue
        apply_edge(edge, dependencies, duplicates, conflicts)
    return conflicts


def similar_outcomes(issues):
    """Similarity holds work for review; it never establishes equivalence."""
    duplicates = set()
    rows = list(issues.values())
    for index, a in enumerate(rows):
        for b in rows[index + 1 :]:
            if a.get("parentId") == b["id"] or b.get("parentId") == a["id"]:
                continue
            if project_key(a) != project_key(b):
                continue
            ta, tb = tokens(a["title"]), tokens(b["title"])
            if len(ta) > 15 and SequenceMatcher(None, ta, tb).ratio() >= 0.90:
                duplicates.add(tuple(sorted((a["id"], b["id"]))))
    return duplicates
