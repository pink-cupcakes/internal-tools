"""Reproducing and regression checks for the intentionally broken fixture."""

import unittest
from parser import parse_numbers


class ParserTests(unittest.TestCase):
    def test_empty_input(self):
        self.assertEqual(parse_numbers(""), [])

    def test_whitespace_input(self):
        self.assertEqual(parse_numbers("  "), [])

    def test_numbers(self):
        self.assertEqual(parse_numbers("1, 2, -3"), [1, 2, -3])

    def test_invalid_token(self):
        with self.assertRaises(ValueError):
            parse_numbers("1,nope")

    def test_empty_middle_field(self):
        with self.assertRaises(ValueError):
            parse_numbers("1,,2")
