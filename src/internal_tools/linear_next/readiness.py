"""Classify ownership, delivery gates, and active-work hazards."""

from .common import active, complete, project_key


def running_issues(issues, diffs):
    running = {ident for ident, row in issues.items() if active(row)}
    for pr in diffs:
        if str(pr.get("status", "")).lower() in ("closed", "merged"):
            continue
        for linked in pr.get("linkedIssues", []):
            issue = linked.get("issue", linked)
            ident = issue.get("identifier") or issue.get("id")
            if ident in issues:
                running.add(ident)
    return running


def workflow_state(issue, running, children):
    ident = issue["id"]
    if issue.get("archivedAt") or issue.get("statusType") in ("completed", "canceled"):
        return "closed", []
    if ident in running:
        return "active", ["Already in progress or linked to an open/draft pull request"]
    if ident in children:
        return "container", ["Tracking ticket; select an executable child instead"]
    if issue.get("statusType") not in ("unstarted", "backlog"):
        return "review", ["Workflow state needs triage"]
    if issue.get("statusType") == "backlog":
        return "backlog", ["Outside the current ready-work scope"]
    if any(
        issue.get(key) for key in ("assigneeId", "assignee", "delegateId", "delegate")
    ):
        return "owned", ["Already assigned or delegated; coordinate with its owner"]
    return "ready", []


def delivery_gates(ident, state, reasons, context):
    issues, dependencies = context["issues"], context["dependencies"]
    pending = sorted(
        key
        for key in dependencies[ident]
        if key not in issues or not complete(issues[key])
    )
    if pending and state == "ready":
        state = "blocked"
        reasons.append("Requires completed deliverables: " + ", ".join(pending))
    for edge in context["edges"]:
        if edge["from"] == ident and edge["kind"] == "requires":
            reasons.append(
                "Inferred prerequisite " + edge["to"] + ": " + edge["evidence"]
            )
    possible = sorted(
        {
            key
            for pair in context["duplicates"]
            if ident in pair
            for key in pair
            if key != ident
        }
    )
    if possible and state == "ready":
        state = "review"
        reasons.append(
            "Potential duplicate; reconcile outcome and scope with "
            + ", ".join(possible)
        )
    return state, pending, possible


def review_gate(state, reasons, review, context):
    if state != "ready":
        return state
    if context["gaps"] or not context["review_valid"]:
        reasons.append(
            "Snapshot incomplete/stale or independent review "
            "does not match this snapshot"
        )
        return "review"
    reviewed = review.get("verdict") == "ready"
    if not (reviewed and review.get("evidence") and review.get("resources")):
        reasons.append(
            review.get("reason")
            or "Needs independent outcome, prerequisite, and ownership review"
        )
        return "review"
    return state


def active_overlaps(ident, resources, review, context):
    issue = context["issues"][ident]
    overlap = []
    for other in context["running"] - {ident}:
        if tuple(sorted((ident, other))) in context["conflicts"]:
            overlap.append(other)
            continue
        if project_key(issue) != project_key(context["issues"][other]):
            continue
        theirs = context["reviews"].get(other, {}).get("resources", [])
        if not theirs or set(resources) & set(theirs):
            overlap.append(other)
    for thread in context["threads"]:
        same_project = project_key(issue) in thread.get("projectIds", [])
        if same_project and not review.get("threadIsolationEvidence"):
            overlap.append("active chat: " + thread["title"])
    return overlap


def classify(ident, issue, context):
    review = context["reviews"].get(ident, {})
    state, reasons = workflow_state(issue, context["running"], context["children"])
    state, pending, possible = delivery_gates(ident, state, reasons, context)
    state = review_gate(state, reasons, review, context)
    resources = sorted(set(review.get("resources", [])))
    if state == "ready":
        overlap = active_overlaps(ident, resources, review, context)
        if overlap:
            state = "conflict"
            reasons.append("Overlaps active work: " + ", ".join(sorted(overlap)))
    if review.get("reason") and review["reason"] not in reasons:
        reasons.append(review["reason"])
    return dict(
        id=ident,
        title=issue["title"],
        url=issue.get("url"),
        project=issue.get("project") or "Workspace",
        state=state,
        reasons=reasons,
        pending=pending,
        resources=resources,
        evidence=review.get("evidence", []),
        duplicates=possible,
    )
