"""Intentionally broken example input, not production router code."""


def parse_numbers(text: str) -> list[int]:
    return [int(field.strip()) for field in text.split(",")]
