"""Plan from supplied evidence without querying or mutating Linear."""

import copy

from .common import MAX_AGE_SECONDS, REQUIRED_COVERAGE, digest, parse_time, utcnow
from .ranking import select_batch
from .readiness import classify, running_issues
from .relations import declared_relations, reviewed_relations, similar_outcomes


def prepare_context(snapshot, review, now):
    issues = {issue["id"]: copy.deepcopy(issue) for issue in snapshot["issues"]}
    if len(issues) != len(snapshot["issues"]):
        raise ValueError("Duplicate issue IDs in snapshot")
    valid = review.get("snapshotDigest") == digest(snapshot)
    coverage = snapshot.get("coverage", {})
    gaps = [key for key in REQUIRED_COVERAGE if coverage.get(key) is not True]
    age = (now - parse_time(snapshot["capturedAt"])).total_seconds()
    if age > MAX_AGE_SECONDS or age < -60:
        gaps.append("fresh snapshot")
    edges = review.get("edges", []) if valid else []
    warnings = list(snapshot.get("warnings", []))
    dependencies, duplicates = declared_relations(issues, gaps)
    conflicts = reviewed_relations(
        issues, edges, dependencies, duplicates, gaps, warnings
    )
    duplicates |= similar_outcomes(issues)
    conflicts |= duplicates
    return dict(
        issues=issues,
        review_valid=valid,
        coverage=coverage,
        gaps=gaps,
        edges=edges,
        warnings=warnings,
        dependencies=dependencies,
        duplicates=duplicates,
        conflicts=conflicts,
        reviews=review.get("issues", {}) if valid else {},
        children={i.get("parentId") for i in issues.values() if i.get("parentId")},
        running=running_issues(issues, snapshot.get("diffs", [])),
        threads=snapshot.get("activeThreads", []),
    )


def plan(snapshot, review, limit=3, now=None):
    """Return reviewed recommendations; a recommendation does not claim work."""
    if not 1 <= limit <= 10:
        raise ValueError("Batch limit must be between 1 and 10")
    now = now or utcnow()
    context = prepare_context(snapshot, review, now)
    statuses = {
        ident: classify(ident, issue, context)
        for ident, issue in context["issues"].items()
    }
    selected = select_batch(
        statuses,
        context["issues"],
        context["dependencies"],
        context["conflicts"],
        limit,
    )
    active_conflicts = [
        list(pair)
        for pair in sorted(context["conflicts"])
        if all(ident in context["running"] for ident in pair)
    ]
    return dict(
        schemaVersion=1,
        capturedAt=snapshot["capturedAt"],
        generatedAt=now.isoformat(),
        snapshotDigest=digest(snapshot),
        coverage=context["coverage"],
        gaps=sorted(set(context["gaps"])),
        reviewValid=context["review_valid"],
        recommendations=selected,
        issues=list(statuses.values()),
        inferredEdges=context["edges"],
        activeConflicts=active_conflicts,
        warnings=context["warnings"],
        maxAgeSeconds=MAX_AGE_SECONDS,
    )
