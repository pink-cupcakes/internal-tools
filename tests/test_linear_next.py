import unittest
from datetime import datetime, timezone, timedelta
from internal_tools.linear_next import digest, plan, render


NOW = datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)


def issue(ident, **kw):
    return dict(
        id=ident,
        title="Distinct task " + ident,
        statusType="unstarted",
        project="Project",
        projectId="p",
        priority={"value": 2},
        relations={"blocks": [], "blockedBy": []},
        createdAt="2026-11-08T00:00:00Z",
        **kw,
    )


def fixture(*items):
    s = dict(
        capturedAt=NOW.isoformat(),
        issues=list(items),
        coverage={
            k: True for k in ("issues", "relations", "comments", "diffs", "threads")
        },
        diffs=[],
        activeThreads=[],
    )
    r = dict(
        snapshotDigest=digest(s),
        issues={
            i["id"]: dict(
                verdict="ready",
                reason="Reviewed contract",
                resources=[i["id"]],
                evidence=["Observed artifact"],
            )
            for i in items
        },
        edges=[],
    )
    return s, r


def state(report, ident):
    return next(i["state"] for i in report["issues"] if i["id"] == ident)


class QueueTests(unittest.TestCase):
    def run_plan(self, s, r):
        return plan(s, r, now=NOW)

    def test_parallel_batch_has_disjoint_resources(self):
        s, r = fixture(issue("A"), issue("B"))
        self.assertEqual(len(self.run_plan(s, r)["recommendations"]), 2)

    def test_same_resource_is_sequenced(self):
        s, r = fixture(issue("A"), issue("B"))
        r["issues"]["B"]["resources"] = ["A"]
        p = self.run_plan(s, r)
        self.assertEqual(len(p["recommendations"]), 1)
        self.assertEqual(state(p, "B"), "sequenced")

    def test_missing_explicit_blocker_is_blocked(self):
        a = issue("A")
        a["relations"]["blockedBy"] = [{"id": "missing"}]
        s, r = fixture(a)
        self.assertEqual(state(self.run_plan(s, r), "A"), "blocked")

    def test_inverse_blocks_and_cycle(self):
        a, b = issue("A"), issue("B")
        a["relations"]["blocks"] = [{"id": "B"}]
        b["relations"]["blocks"] = [{"id": "A"}]
        s, r = fixture(a, b)
        p = self.run_plan(s, r)
        self.assertEqual([state(p, x) for x in ["A", "B"]], ["blocked", "blocked"])

    def test_canceled_is_not_delivered(self):
        a, b = issue("A"), issue("B")
        a["relations"]["blockedBy"] = [{"id": "B"}]
        b["statusType"] = "canceled"
        s, r = fixture(a, b)
        self.assertEqual(state(self.run_plan(s, r), "A"), "blocked")

    def test_completed_prerequisite_is_satisfied(self):
        a, b = issue("A"), issue("B")
        a["relations"]["blockedBy"] = [{"id": "B"}]
        b["statusType"] = "completed"
        s, r = fixture(a, b)
        self.assertEqual(state(self.run_plan(s, r), "A"), "ready")

    def test_inferred_missing_dependency(self):
        s, r = fixture(issue("A"), issue("B"))
        r["edges"] = [
            dict(
                kind="requires",
                **{"from": "A", "to": "B"},
                evidence="Consumer needs producer contract",
            )
        ]
        self.assertEqual(state(self.run_plan(s, r), "A"), "blocked")

    def test_false_gate_requires_artifact(self):
        a, b = issue("A"), issue("B")
        a["relations"]["blockedBy"] = [{"id": "B"}]
        s, r = fixture(a, b)
        r["edges"] = [
            dict(
                kind="satisfied_gate",
                **{"from": "A", "to": "B"},
                evidence="Stable interface inspected",
            )
        ]
        with self.assertRaises(ValueError):
            self.run_plan(s, r)
        r["edges"][0]["artifact"] = "repo/schema.sql@sha256:abc"
        self.assertEqual(state(self.run_plan(s, r), "A"), "ready")

    def test_active_unknown_footprint_holds_project(self):
        a, b = issue("A"), issue("B")
        b["statusType"] = "started"
        s, r = fixture(a, b)
        del r["issues"]["B"]
        self.assertEqual(state(self.run_plan(s, r), "A"), "conflict")

    def test_draft_pr_holds_issue_even_if_todo(self):
        s, r = fixture(issue("A"))
        s["diffs"] = [{"status": "draft", "linkedIssues": [{"id": "A"}]}]
        r["snapshotDigest"] = digest(s)
        self.assertEqual(state(self.run_plan(s, r), "A"), "active")

    def test_active_chat_blocks_unknown_isolation(self):
        s, r = fixture(issue("A"))
        s["activeThreads"] = [dict(title="Worker", projectIds=["p"])]
        r["snapshotDigest"] = digest(s)
        self.assertEqual(state(self.run_plan(s, r), "A"), "conflict")

    def test_parent_is_tracking_not_duplicate(self):
        s, r = fixture(issue("A"), issue("B", parentId="A"))
        self.assertEqual(state(self.run_plan(s, r), "A"), "container")
        self.assertEqual(state(self.run_plan(s, r), "B"), "ready")

    def test_duplicate_outcomes_held_without_merging(self):
        a, b = issue("A"), issue("B")
        a["title"] = b["title"] = "Build deterministic conversion library"
        s, r = fixture(a, b)
        p = self.run_plan(s, r)
        self.assertEqual(len(p["recommendations"]), 0)
        self.assertEqual([state(p, x) for x in ["A", "B"]], ["review", "review"])

    def test_backlog_and_owned_are_excluded(self):
        a, b = issue("A"), issue("B")
        a["statusType"] = "backlog"
        b["delegateId"] = "worker"
        s, r = fixture(a, b)
        self.assertFalse(self.run_plan(s, r)["recommendations"])

    def test_snapshot_drift_invalidates_review(self):
        s, r = fixture(issue("A"))
        s["issues"][0]["description"] = "New scope"
        self.assertFalse(self.run_plan(s, r)["recommendations"])

    def test_missing_coverage_and_staleness_withhold(self):
        for field in ["comments", "threads", "diffs", "relations", "issues"]:
            s, r = fixture(issue("A"))
            s["coverage"][field] = False
            r["snapshotDigest"] = digest(s)
            self.assertFalse(self.run_plan(s, r)["recommendations"])
        s, r = fixture(issue("A"))
        s["capturedAt"] = (NOW - timedelta(hours=3)).isoformat()
        r["snapshotDigest"] = digest(s)
        self.assertFalse(self.run_plan(s, r)["recommendations"])

    def test_issue_text_cannot_break_script(self):
        s, r = fixture(issue("A"))
        s["issues"][0]["title"] = "</script><script>alert(1)</script>"
        r["snapshotDigest"] = digest(s)
        result = render(self.run_plan(s, r))
        self.assertNotIn("</script><script>alert(1)", result)
        self.assertIn("\\u003c/script", result)


if __name__ == "__main__":
    unittest.main()
