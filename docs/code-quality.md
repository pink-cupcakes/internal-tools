# Static code checks

Install the pre-commit hook once per checkout:

```sh
uv sync --locked --no-editable
uv run --locked --no-editable pre-commit install
```

Each `git commit` checks staged Python files with Ruff and the function-length hook. A violation blocks the commit without changing files. The Ruff hook uses the official integration pinned to the same version as `uv.lock`; update both versions together when upgrading Ruff. CI also checks the full repository.

Run the hook manually on tracked files, or run the full static analysis and tests:

```sh
uv run --locked --no-editable pre-commit run --all-files
uv run --locked --no-editable ruff check .
uv run --locked --no-editable python scripts/check_function_length.py
uv run --locked --no-editable python scripts/generate_model_router_schemas.py --check
uv run --locked --no-editable python -m unittest discover -s tests -v
```

Limits are configured in `pyproject.toml`:

- **88 characters per line:** Ruff's `E501` rule checks width. Use `uv run --locked --no-editable ruff format .` to wrap code; split long strings manually when necessary. Ruff's documented exceptions, such as long URLs, still apply.
- **50 lines per function:** `scripts/check_function_length.py` parses Python with the standard library's AST. It measures from `def` or `async def` through the final statement, including the signature, docstrings, comments, and blank lines within that span. Decorators above the definition are excluded. Methods and nested functions are checked too. This is an actual line limit; Ruff's `PLR0915` counts statements instead.
- **Cyclomatic complexity of 10:** Ruff's `C901` rule measures control-flow paths: a straight-line function starts at 1, and decision points add to the score.

These checks cover source, tests, Python examples, and the checker itself. Generated build files and local environments are excluded. Ruff also checks basic syntax and correctness (`E4`, `E7`, `E9`, `F`), and `RUF100` catches unused suppression comments. No variable-naming rules are enabled.

CI also checks routing JSON Schemas against the current models. After a model change, regenerate them with `uv run --locked --no-editable python scripts/generate_model_router_schemas.py`.

The threshold is a review prompt, not a complete measure of good design. Following Martin Fowler's *Refactoring: Improving the Design of Existing Code*, extract a cohesive operation into a well-named function, name complicated conditions, and use guard clauses to make exceptional paths clear. Make small changes and rerun behavior tests after each step. Guard clauses can reduce nesting without lowering cyclomatic complexity; avoid splitting code merely to lower a score. Keep the limit explicit rather than raising it to accommodate a new violation. Any necessary exception should use a rule-specific `# noqa: C901` on the function definition with a reason.

References: [Ruff's pre-commit integration](https://docs.astral.sh/ruff/integrations/#pre-commit), [line-length rule](https://docs.astral.sh/ruff/rules/line-too-long/), [statement-count rule](https://docs.astral.sh/ruff/rules/too-many-statements/), [complexity rule](https://docs.astral.sh/ruff/rules/complex-structure/), [threshold configuration](https://docs.astral.sh/ruff/settings/#lint_mccabe_max-complexity), and Fowler's [Extract Function](https://refactoring.com/catalog/extractFunction.html), [Decompose Conditional](https://refactoring.com/catalog/decomposeConditional.html), and [Replace Nested Conditional with Guard Clauses](https://refactoring.com/catalog/replaceNestedConditionalWithGuardClauses.html).

