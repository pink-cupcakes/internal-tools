"""Exercise installed CLI dispatch, report publication, and input isolation."""

from contextlib import redirect_stderr, redirect_stdout
from datetime import timedelta
from importlib.resources import files
import io
import json
from pathlib import Path
import tempfile
import unittest

from internal_tools.cli import main
from internal_tools.linear_next import plan, publish, render
from test_linear_next import NOW, fixture, issue


class LinearNextStorageTests(unittest.TestCase):
    def test_installed_resources_include_view_and_refresh_protocol(self):
        resources = files("internal_tools.linear_next")
        self.assertTrue(resources.joinpath("REFRESH.md").is_file())
        snapshot, review = fixture(issue("A"))
        html = render(plan(snapshot, review, now=NOW))
        self.assertNotIn("/*QUEUE_DATA*/null", html)
        self.assertNotIn("/Users/andy/", html)

    def test_older_publication_preserves_all_existing_outputs(self):
        snapshot, review = fixture(issue("A"))
        report = plan(snapshot, review, now=NOW)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            publish(report, directory)
            before = {p.name: p.read_bytes() for p in directory.glob("*.*")}
            older = dict(report, capturedAt=(NOW - timedelta(hours=1)).isoformat())
            with self.assertRaisesRegex(ValueError, "newer snapshot"):
                publish(older, directory)
            after = {p.name: p.read_bytes() for p in directory.glob("*.*")}
            self.assertEqual(before, after)

    def test_publication_escapes_issue_text_and_does_not_mutate_report(self):
        snapshot, review = fixture(issue("A"))
        report = plan(snapshot, review, now=NOW)
        report["issues"][0]["title"] = "</script><script>alert(1)</script>"
        with tempfile.TemporaryDirectory() as temporary:
            output = publish(report, temporary, Path(temporary) / "inline.html")
            self.assertNotIn("queueDirectory", report)
            self.assertEqual(output["queueDirectory"], str(Path(temporary).resolve()))
            html = (Path(temporary) / "widget.html").read_text()
            self.assertNotIn("</script><script>alert(1)</script>", html)
            self.assertEqual(
                (Path(temporary) / "inline.html").read_text(), render(output)
            )

    def test_shared_cli_digest_build_and_status_from_external_directory(self):
        snapshot, review = fixture(issue("A"))
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name, data in (("snapshot", snapshot), ("review", review)):
                (directory / f"{name}.json").write_text(json.dumps(data))
            inputs = [
                (directory / f"{name}.json").read_bytes()
                for name in ("snapshot", "review")
            ]
            for action in ("digest", "build", "status"):
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    result = main(["linear-next", action, "--directory", temporary])
                self.assertEqual(result, 0)
                self.assertTrue(stdout.getvalue().strip())
            self.assertTrue((directory / "widget.html").is_file())
            self.assertEqual(
                inputs,
                [
                    (directory / f"{name}.json").read_bytes()
                    for name in ("snapshot", "review")
                ],
            )

    def test_missing_inputs_report_error_without_creating_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                result = main(["linear-next", "build", "--directory", temporary])
            self.assertEqual(result, 1)
            self.assertIn("linear-next:", stderr.getvalue())
            self.assertFalse((Path(temporary) / "widget.html").exists())

    def test_duplicate_ids_and_invalid_limits_are_rejected(self):
        snapshot, review = fixture(issue("A"))
        snapshot["issues"].append(dict(snapshot["issues"][0]))
        with self.assertRaisesRegex(ValueError, "Duplicate issue IDs"):
            plan(snapshot, review, now=NOW)
        for limit in (0, 11):
            with self.assertRaisesRegex(ValueError, "Batch limit"):
                plan(snapshot, review, limit=limit, now=NOW)
