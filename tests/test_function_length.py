from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.check_function_length import check_file

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHECKER_PATH = PROJECT_ROOT / "scripts/check_function_length.py"


class FunctionLengthTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.source_path = Path(directory.name) / "probe.py"

    def check_source(self, source, max_lines=50):
        self.source_path.write_text(source)
        return check_file(self.source_path, max_lines)

    def test_exact_limit_passes_and_one_more_line_fails(self):
        for length, should_fail in ((50, False), (51, True)):
            source = "def probe():\n" + "    value = 1\n" * (length - 2)
            source += "    return value\n"
            with self.subTest(length=length):
                errors = self.check_source(source)
                self.assertEqual(bool(errors), should_fail)

    def test_multiline_signature_docstring_comments_and_blanks_count(self):
        source = (
            "def probe(\n"
            "    value,\n"
            "):\n"
            '    """Documentation."""\n'
            "    # Keep the value.\n"
            "\n"
            "    return value\n"
        )
        errors = self.check_source(source, max_lines=6)
        self.assertEqual(len(errors), 1)
        self.assertIn("has 7 lines (maximum 6)", errors[0])

    def test_async_methods_are_checked_without_counting_decorators(self):
        source = (
            "class Worker:\n"
            "    @staticmethod\n"
            "    async def fetch():\n"
            "        value = 1\n"
            "        value += 1\n"
            "        return value\n"
        )
        self.assertEqual(self.check_source(source, max_lines=4), [])
        errors = self.check_source(source, max_lines=3)
        self.assertIn("function 'fetch' has 4 lines", errors[0])

    def test_nested_functions_are_also_checked(self):
        source = (
            "def outer():\n"
            "    def inner():\n"
            "        value = 1\n"
            "        value += 1\n"
            "        return value\n"
            "    return inner()\n"
        )
        errors = self.check_source(source, max_lines=3)
        self.assertEqual(len(errors), 2)
        self.assertTrue(any("function 'inner'" in error for error in errors))

    def test_invalid_syntax_and_missing_files_fail(self):
        self.assertTrue(self.check_source("def broken(:\n"))
        self.source_path.unlink()
        self.assertTrue(check_file(self.source_path, 50))

    def test_command_exits_with_failure_for_long_functions(self):
        self.source_path.write_text(
            "def probe():\n" + "    value = 1\n" * 49 + "    return value\n"
        )
        result = subprocess.run(
            [sys.executable, str(CHECKER_PATH), str(self.source_path)],
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("has 51 lines (maximum 50)", result.stdout)
