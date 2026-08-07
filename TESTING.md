# TESTING.md — Testing Standards: Prompt Compiler

> Version: 2.0.0 | Date: 2026-08-06
> Defines how tests are written, organized, and executed in Prompt Compiler. The central principle: tests are written BEFORE the code, not after. A test written after the implementation verifies that the code does what it does — not that it does what it should do.
>
> **Current state:** `pyproject.toml` already configures pytest (`testpaths = ["tests"]`, `python_files = ["test_*.py"]`) but no `tests/` directory exists yet (SPEC.md §5, §10 roadmap phase 15-16). This document defines the standard the first and every subsequent test must follow — it is not describing an existing suite, it is the spec for building one.

---

## 0. Non-negotiable: run tests after every code change

**After any modification to `app/` source files, run the affected tests before reporting the task as done.**

```bash
# Run affected module tests
pytest tests/unit/<module>/test_<file>.py -v

# Run the full test suite (quick check)
pytest tests/ -q
```

Use the project's virtual environment interpreter — not the system Python. On Windows: `.venv/Scripts/python.exe -m pytest ...`; on Unix: `.venv/bin/python -m pytest ...`

This rule is not part of the Red-Green-Refactor cycle — it is a floor. Even if TDD was not followed for a change, this check must happen before the task is reported as done. An observed, passing test run is the minimum bar for "complete."

---

## 1. Philosophy: Test-Driven Development (TDD)

### The Red-Green-Refactor cycle

Every change involving new code follows this cycle, without exception:

```
[RED]    Write the test that describes the expected behavior.
         Run the test → it must FAIL (the code does not exist yet, or the bug reproduces).
         If the test passes without code, the test is written incorrectly.
              ↓
[GREEN]  Write the minimum code needed to make the test pass.
         Nothing more. No abstractions, no "while I'm here" additions.
              ↓
[REFACTOR] Clean up the code without breaking the tests.
           Apply CLAUDE.md §2 (Simplicity First) and §3 (Surgical Changes).
           Run the tests after each refactor change.
              ↓
         [Repeat for the next behavior]
```

### Why tests first

A test written before the implementation:
- **Defines the contract** — what YAML input it accepts, what `AgentSpec`/rendered output it produces, what error it raises.
- **Forces API design** — if it's hard to construct a synthetic `AgentSpec` or fixture YAML for a test, the schema or loader interface is poorly designed.
- **Is executable documentation** — describes expected DSL behavior in a verifiable way, more reliable than prose in `AGENT_CREATION_GUIDE.md` alone.
- **Detects regressions** — if a schema, loader, validator, or renderer changes and breaks the behavior, the test fails immediately instead of silently producing a malformed prompt.

### What TDD is not

- It is not writing all tests for a module before writing any line of code.
- It is not writing perfect tests from the start.
- It is not test coverage as a quality metric (100% coverage with trivial tests is worthless).
- It is: **one behavior → one test that describes it → the code that implements it**.

---

## 2. Test organization

### Organization principle

**The test structure mirrors the `app/` source structure.** `app/renderers.py` → `tests/unit/app/test_renderers.py`. There are no exceptions to this rule.

### Directory structure

```
tests/
├── conftest.py                  ← fixtures ONLY if genuinely cross-suite
│                                   (e.g. a minimal valid AgentSpec builder used by
│                                   multiple test files). Do not put single-module
│                                   fixtures here.
├── fixtures/                     ← synthetic YAML agent definitions used by tests
│   └── minimal_agent/            ← smallest possible valid agent_dir (manifest +
│                                    required includes) — never real agents/ content
├── unit/
│   └── app/
│       ├── test_schemas.py       ← FlowObjectBase per-type invariants, regex constraints
│       ├── test_loaders.py       ← merging, subflow instantiation, namespacing, alias resolution
│       ├── test_validators.py    ← one test group per validator (reachability, cycles,
│       │                           dangling GO_TO, duplicate ids, placeholder consistency,
│       │                           FAQ resume targets, compliance checkers)
│       ├── test_renderers.py     ← exact rendered Markdown/JSON fragments for known input
│       ├── test_classifier.py    ← prompt-bound vs reference-bound split (SSOT §10)
│       ├── test_deduplicator.py  ← duplicate-rule detection, report-only (no mutation)
│       ├── test_compiler.py      ← compile_agent() orchestration: stage order, no file I/O,
│       │                           renders-even-on-validation-failure behavior
│       ├── test_utils.py         ← VAR_RE / CONST_RE / SLOT_RE / GOTO_RE / PARAM_RE /
│       │                           alias regexes, namespace builders
│       ├── test_mermaid_parser.py    ← flowchart source → SubflowTemplate scaffold shape mapping
│       └── test_mermaid_diagrams.py  ← AgentSpec → Mermaid export (structure, not pixel output)
└── integration/
    ├── test_compile_minimal_agent.py   ← end-to-end: fixture agent_dir → compile_agent() →
    │                                      assert exact system_prompt.md / reference_asset content
    ├── test_compile_with_subflows.py   ← end-to-end with a SubflowTemplate instance: namespacing
    │                                      and alias resolution across the full pipeline
    └── test_compile_compliance_profile.py  ← a fixture agent that violates a medical_es rule →
                                               assert the validation report contains it
```

### Organization rules (non-negotiable)

1. **One test file per source file** — `renderers.py` → `test_renderers.py`. Not a single `test_everything.py` mixing domains.
2. **Integration tests are grouped by functional domain** — a full compile flow, not by module. `test_compile_minimal_agent.py`, not `test_compiler_and_renderers_and_validators.py`.
3. **Prohibited:** test files with generic names (`test_utils_misc.py`, `test_various.py` — note `test_utils.py` itself is fine since it mirrors `app/utils.py`).
4. **Prohibited:** tests of different levels (unit + integration) in the same file.
5. **`conftest.py`** only holds fixtures genuinely shared across multiple suites (e.g. a `minimal_agent_dir` fixture reused by several integration tests). Fixtures for a single module go in that module's test file.
6. **Fixture YAML lives in `tests/fixtures/`, never in `agents/`.** `agents/` is a separate, nested, gitignored repo (SPEC.md §8 invariant 10 / CLAUDE.md §5.2.9) — tests must never read from it, and must never write synthetic agent definitions into it.

---

## 3. Types of tests and when to use each

### Unit tests (`tests/unit/`)

**What they test:** A single function or class in `app/`, in complete isolation.

**Required characteristics:**
- No network, no real filesystem paths — use `tmp_path` (pytest) or in-memory YAML strings (`yaml.safe_load(...)`) for anything loader-related.
- Data is synthetic, defined within the test or drawn from `tests/fixtures/`.
- Each test verifies a single behaviour — multiple assertions for the same behaviour are fine.
- Execution time < 100ms per test (no real compiles in unit tests — that's what integration tests are for).

**When required:**
- For every new or changed public function/class in `app/`.
- Minimum: base case test + one relevant edge case.

**Minimum unit tests by component type:**

| Component type | Minimum required tests |
|---|---|
| Pydantic schema / `model_validator` | Valid construction succeeds; each documented per-type invariant violation raises |
| Regex helper (`app/utils.py`) | Matches the documented valid form; rejects at least one documented invalid form |
| Loader function | Produces the expected merged `AgentSpec` shape from minimal fixture YAML; a known-bad input (unresolved alias) raises the documented error |
| Validator function | A synthetic `AgentSpec` that violates the rule is flagged; one that satisfies it is not (no false positive) |
| Renderer function (`_render_*`) | Given a known small input, output matches an exact expected string/fragment |
| Compliance checker | A fixture violating the `medical_es` rule is flagged with the correct severity from the profile YAML |

### Integration tests (`tests/integration/`)

**What they test:** The full `compile_agent()` pipeline, or a meaningful slice of it (e.g. load → validate without rendering).

**Characteristics:**
- Uses real `compile_agent()` against fixture agent directories under `tests/fixtures/`.
- Synthetic data throughout.
- Execution time may be in the seconds range, still no network/GPU.

**When required:**
- For every change that could affect the interaction between two or more pipeline stages (e.g. a loader change that only breaks once the renderer consumes its output).
- For any DSL grammar change (per FRAMEWORK.md §5.9 — a grammar change is incomplete without at least one integration test proving the full pipeline still compiles correctly).

### Property-based / round-trip tests (for critical components)

**What they test:** Properties that must hold for any valid input, not just the chosen examples.

**When to use:**
- Determinism: compiling the same fixture agent twice produces byte-identical output.
- Alias resolution: for any valid `@instance.export` reference, resolution always succeeds; for any dangling one, `_validate_no_unresolved_aliases` always raises.
- Mermaid round-trip: a `SubflowTemplate` scaffolded from a Mermaid diagram, when exported back to Mermaid, preserves node count and shape-to-type mapping.

```python
def test_compile_is_deterministic(minimal_agent_dir, default_params):
    result_a = compile_agent(minimal_agent_dir, default_params)
    result_b = compile_agent(minimal_agent_dir, default_params)
    assert result_a.system_prompt == result_b.system_prompt
    assert result_a.reference_asset_json == result_b.reference_asset_json
```

---

## 4. Naming conventions

### Files

- Format: `test_<module_name>.py` in lowercase with underscores.
- The name mirrors the source module: `mermaid_parser.py` → `test_mermaid_parser.py`.
- Integration tests describe the flow: `test_compile_minimal_agent.py`, `test_compile_with_subflows.py`.
- **Prohibited:** phase numbers in the name (`test_phase15_schemas.py`).

### Test functions

- Format: `test_<subject>_<behavior>` in lowercase.
- Describes the behavior, not the implementation: `test_question_node_requires_wait_yes` (not `test_model_validator_runs`).
- For parametrized behaviors: the base name describes the generic behavior.

```python
# Correct
def test_terminal_node_requires_final_yes():
def test_subflow_instance_namespaces_state_ids_with_double_underscore():
def test_validator_flags_unresolved_instance_export_alias():

# Incorrect
def test_validate():                 # too generic
def test_1():                        # no meaning
def test_schemas_v2():               # version number in the name
```

### Fixtures and helpers

- Fixtures shared within a file: `@pytest.fixture` local to the file.
- Private helpers (not tests): prefix `_`: `_minimal_manifest_yaml()`, `_agent_spec_with_state(...)`.
- Do not use `TestXxx` classes — tests are top-level functions.

```python
# Correct
@pytest.fixture
def minimal_agent_spec():
    """Smallest AgentSpec with one start state and one terminal state."""
    return _build_agent_spec(states=[_start_state(), _terminal_state()])

def _build_agent_spec(states):
    return AgentSpec(agent_id="test_agent", states=states, ...)

def test_compile_agent_never_writes_files(minimal_agent_dir, default_params, tmp_path):
    ...
```

---

## 5. Test quality rules (non-negotiable)

1. **No real filesystem paths** — except through `tempfile.TemporaryDirectory()` or `tmp_path` (pytest), or in-memory fixture strings. No hardcoded paths into `agents/` or `dist/`.
2. **No external services** — no network, no databases, no APIs.
3. **No ordering dependency between tests** — each test can run in isolation and in any order.
4. **Synthetic data only** — fixture YAML built in `tests/fixtures/` or generated in-test. Never real `agents/defs/`/`agents/shared/` content, and never real PII (CLAUDE.md §5.2.10).
5. **One behaviour per test** — multiple assertions confirming the same behaviour are fine; unrelated properties are separate tests.
6. **The test fails without the code** — if the test passes before writing the fix/feature, the test is wrong.
7. **Determinism** — the compiler is documented as deterministic (SPEC.md §8 invariant 9); if a test needs randomness for input generation, fix the seed.

---

## 6. CI/CD pipeline

### Local verification pipeline (before each commit)

```bash
# 1. Linting and formatting
ruff check .        # linting (select = ["E","F","I","B","UP","RUF"], line-length 110)

# 2. Tests
pytest tests/unit/ -q           # unit tests (fast)
pytest tests/integration/ -q    # integration tests

# 3. Coverage (optional, non-blocking)
pytest --cov=app --cov-report=term-missing tests/
```

Note: this project has no `mypy`/static type-checking step configured in `pyproject.toml` today — don't invent one in a task unless explicitly asked; flag it as a possible future addition instead (SPEC.md §12 open question candidate).

### CI pipeline (does not exist yet — SPEC.md §10 roadmap phase 17)

Target shape for `.github/workflows/ci.yml` once created:

```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.11", "3.12"]
    steps:
      - name: Lint
        run: ruff check .
      - name: Unit tests
        run: pytest tests/unit/ -q
      - name: Integration tests
        run: pytest tests/integration/ -q
```

**Rule (once CI exists):** no PR is merged if CI fails. No exceptions.

---

## 7. Structure of a well-written test

```python
# tests/unit/app/test_validators.py

import pytest

from app.schemas import AgentSpec
from app.validators import validate_agent_spec


# ---------------------------------------------------------------------------
# Local fixtures — only if used in more than one test function in this file
# ---------------------------------------------------------------------------

@pytest.fixture
def spec_with_dangling_goto():
    """AgentSpec whose only state routes to a state id that doesn't exist."""
    return _agent_spec_with_route_to("STATE_DOES_NOT_EXIST")


# ---------------------------------------------------------------------------
# Base case tests
# ---------------------------------------------------------------------------

def test_valid_spec_has_no_reachability_errors(minimal_agent_spec):
    report = validate_agent_spec(minimal_agent_spec)
    assert not report.has_errors


def test_dangling_goto_is_flagged_as_error(spec_with_dangling_goto):
    report = validate_agent_spec(spec_with_dangling_goto)
    assert any("STATE_DOES_NOT_EXIST" in e.message for e in report.errors)


# ---------------------------------------------------------------------------
# Edge case tests
# ---------------------------------------------------------------------------

def test_self_looping_question_without_retry_counter_warns():
    spec = _agent_spec_with_self_looping_question(has_retry_counter=False)
    report = validate_agent_spec(spec)
    assert any("retry" in w.message.lower() for w in report.warnings)


@pytest.mark.parametrize("severity", ["error", "warning"])
def test_compliance_checker_reports_configured_severity(severity, compliance_profile_factory):
    profile = compliance_profile_factory(rule_severity=severity)
    spec = _agent_spec_violating_compliance_rule()
    report = validate_agent_spec(spec, compliance_profile=profile)
    assert report.findings[0].severity == severity


def _agent_spec_with_route_to(target_state_id: str) -> AgentSpec:
    ...
```

---

## 8. What does NOT belong in tests

- **Business/domain logic re-implementation** — tests verify behavior, they do not re-implement the validator or renderer logic to check itself.
- **Prints and logs** — tests are silent by default.
- **Dependencies between tests** — if test B requires test A to have run first, both are poorly designed.
- **Tests that always pass** — if a test can never fail, it has no value.
- **Tests of internal implementation** — a private `_render_*` helper's exact call sequence is not a test target; its observable output through the public `renderers.py` function is.
- **Real agent content** — never copy a fragment of an actual flow into a test fixture "because it's realistic." Build a synthetic minimal equivalent instead.

---

## 9. Rules for adding new tests

| Type of addition | Unit test required | Integration test required |
|---|---|---|
| New Pydantic schema field / node-type invariant | `tests/unit/app/test_schemas.py` | Only if it changes what a valid minimal agent must declare |
| New/changed validator | `tests/unit/app/test_validators.py` | Only if it depends on cross-stage state (e.g. classifier output) |
| New/changed `_render_*` function or template block | `tests/unit/app/test_renderers.py` | `tests/integration/test_compile_minimal_agent.py` if it changes the compiled prompt shape |
| New compliance checker or profile rule | `tests/unit/app/test_validators.py` | `tests/integration/test_compile_compliance_profile.py` |
| Loader/namespacing/alias-resolution change | `tests/unit/app/test_loaders.py` | `tests/integration/test_compile_with_subflows.py` |
| New regex or utility helper | `tests/unit/app/test_utils.py` | — |
| Mermaid parser/exporter change | `tests/unit/app/test_mermaid_parser.py` or `test_mermaid_diagrams.py` | — |
| CLI/TUI entry-point change (`build_prompt.py`, `main.py`) | — (thin wrappers; test via `compiler.py`) | Only if it changes what gets written to `dist/` |

---

*This document complements FRAMEWORK.md (process), EXPERTS.md (@qa), and CLAUDE.md (policies). Update when CI/CD tooling changes, when a new `app/` module appears that requires specific tests, or when a category of bug could have been detected by a type of test not covered here.*
