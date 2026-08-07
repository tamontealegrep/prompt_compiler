# CLAUDE.md — Development Policies: Prompt Compiler

> Version: 2.0.0 | Date: 2026-08-06 | Project: Prompt Compiler
> This file is read at the start of every development session. Rules here apply always, without exception. If there is a conflict between these rules and a specific request, this file wins.

---

## 0. Language

**All code, identifiers, comments, docstrings, commit messages, and technical documentation are written in English.** No exceptions. The working language for code artifacts is English regardless of the spoken language of the team.

This applies to:
- Variable names, function names, class names, module names
- Inline comments
- Docstrings
- Commit messages and PR descriptions
- Error messages raised by the code (validator/loader error strings)

Does NOT apply to: content authored *inside* agent YAML (`say`, `say_verbatim`, FAQ answers, handler copy) and Spanish-language project docs (`PATTERN_GUIDE.md`, `faq.md`, `handlers.md`) — those follow the target agent's or the doc's own language, since they are consulting/domain material, not compiler code.

---

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

This especially applies to changes touching the DSL contract (`schemas.py`, `templates/*.j2`, `app/utils.py` regexes): a silent interpretation of an ambiguous DSL rule becomes a de-facto spec that every compiled agent then depends on.

The cost of pausing to confirm is low. The cost of going in the wrong direction can be very high — a wrong DSL assumption compiles silently and only breaks at LLM runtime, in front of a real caller.

---

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No new configurable knobs (CLI flags, `CompilationParams` fields) that nobody requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Sanity check: would a senior engineer say this is overcomplicated? If yes, simplify.

---

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently (Pydantic v2 `StrictModel` conventions, `_render_*` private helper naming, `_COMPLIANCE_CHECKERS` registry pattern).
- If you notice unrelated dead code, mention it — don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked explicitly (e.g. the legacy `configs/{id}/...` → `agents/defs/{id}/...` path translation in `loaders._resolve_path` is intentional backward-compat, not dead code).

The test: every changed line should trace directly to the user's request.

---

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add a new validator" → "write a test with a synthetic `AgentSpec` that violates the rule, confirm it fails without the code, then make it pass"
- "Fix a rendering bug" → "write a test that renders a minimal spec and asserts the exact Markdown/DSL output, then make it pass"
- "Change the DSL grammar" → "update `schemas.py` + the relevant `_render_*` function + `templates/*.j2` + `AGENT_CREATION_GUIDE.md` together; nothing shippable if only one of the four changes"

For multi-step tasks, state the plan:
```
1. [Step] → verify: [how]
2. [Step] → verify: [how]
3. [Step] → verify: [how]
```

---

## 5. Domain Hard Rules

> These rules are specific to Prompt Compiler and its domain (a compiler that produces system prompts and reference assets for medical conversational agents). They are non-negotiable.

### 5.1 Universal rules (apply to all projects)

1. **Never fabricate data or figures.** If information is missing, mark it with an explicit placeholder (`{{PENDING}}`, `TODO`, `???`) and ask — never fill it in by inference. This applies with extra force to legal/medical content: never invent a compliance rule, a legal citation, or a clinical fact that isn't already sourced in the repo or given by the user.
2. **Don't self-resolve conflicts.** Neither contradictions in the input material nor disagreements between panel experts are resolved alone: they are documented and presented to the decision-maker.
3. **Input is immutable.** Agent source YAML (`agents/defs/`, `agents/shared/`), once loaded, is never modified as a side effect of compiling. A compile run only ever writes to `dist/<agent_id>/`. Any change to source YAML is an explicit, separate edit the author asked for.
4. **Never skip templates.** The corresponding template (`AGENT_CREATION_GUIDE.md` pattern, `PATTERN_GUIDE.md` compact-flow pattern) is loaded and followed before generating new agent content for that stage.
5. **The changelog is always recorded, at the top of the file (most recent first).** Applies to `STATUS.yaml`.
6. **Propose and wait.** Any stage advance, substantial edit, or closure of an open point is proposed and awaits confirmation — never executed and reported after the fact.
7. **Notify cross-references.** A change to `schemas.py` (DSL grammar) must be flagged as affecting `renderers.py`, `templates/*.j2`, `validators.py`, and `AGENT_CREATION_GUIDE.md` — these four are coupled and drift silently if only one is updated.

### 5.2 Project-specific rules

1. **`app/classifier.py`'s prompt-vs-reference-asset split is fixed, not configurable.** It implements SSOT §10. Never add a CLI flag, `CompilationParams` field, or per-agent override that lets two agents classify the same field differently — that would let compiled agents diverge structurally from the same source schema.
2. **`compile_agent()` in `app/compiler.py` never writes files.** File I/O belongs to `build_prompt.py` (and `main.py`'s TUI). Keeping the compiler pure keeps it testable without a filesystem.
3. **Rendering happens even when validation fails.** `compile_agent()` renders regardless of validator errors so authors can inspect a broken prompt while debugging. Don't "fix" this by short-circuiting on validation failure — that's the documented behavior, not a bug.
4. **Diagnostics are always written; compiled artifacts are gated.** `validation_report.md`, `deduplication_report.md`, and `orphan_states_report.md` are written on every run. `system_prompt.md` / `reference_asset.*` are only written on a clean validation pass, unless the caller explicitly opts into `--fail-on-warnings`-style overrides. Don't collapse this distinction.
5. **Naming regexes are load-bearing, not cosmetic.** `agent_id` / slot / tool names = `lower_snake_case` (`LOWER_SNAKE_RE`); `CONSTANT` / `STATE_ID` / `HANDLER_ID` / `FAQ_ID` = `UPPER_SNAKE_CASE`; compliance `rule_id` = `^[A-Z]+_[0-9]+$` (`RULE_ID_RE`). A change that weakens or bypasses one of these regexes is a breaking DSL change — treat it as such (§7 of this file, `@architect` + `@dsl-expert` review required).
6. **Subflow namespacing uses a double underscore, always.** Instantiated subflow states become `NAMESPACE__STATE_ID` (upper); slots become `namespace__slot` (lower). Never introduce a single-underscore or dot-based namespacing variant — it collides with existing compiled agents' `GO_TO` targets.
7. **`say_verbatim` is a compliance control, not a style preference.** It flags content the LLM must reproduce exactly (legal disclosures, medical-risk language, pricing) rather than paraphrase. Never default it to `false` for new content categories without asking — silently making legally-sensitive copy "flexible" is a compliance regression, not a simplification.
8. **Compliance severity lives in `profiles/compliance/*.yaml`, not in Python.** Adding or changing a compliance rule's severity (error vs. warning) is a YAML edit to the relevant profile, never a hardcoded branch in `validators.py`. If a rule genuinely needs new logic, add a checker function to the `_COMPLIANCE_CHECKERS` registry — don't special-case it inline in `validate_agent_spec()`.
9. **`agents/` is a separate, nested git repository, gitignored by this repo.** Never assume `agents/defs/` or `agents/shared/` content is tracked by (or committable through) the main `prompt_compiler` repo's git. Never run repo-wide destructive git operations (`git clean -fd`, `git checkout .`) without checking whether they would touch `agents/`.
10. **No real PII in the repo.** No real lead/patient names, phone numbers, national IDs (cédula), or medical history belongs in fixtures, tests, example YAML, or documentation — including screenshots or pasted transcripts. Use synthetic data (`+57 300 000 0000`-style, fictitious names) exclusively, even when illustrating a bug report.
11. **Compilation is deterministic.** Identical source YAML must always produce byte-identical `system_prompt.md` / `reference_asset.*` output. No timestamps, random IDs, or environment-dependent ordering (e.g. unsorted dict iteration into rendered output) may leak into generated artifacts.
12. **After modifying any source file in `app/`, run the affected tests before reporting the task complete** — a change is not done until test output is observed and passing. Use the project's virtual environment interpreter (`.venv/Scripts/python.exe` on Windows), not the system Python. (See TESTING.md — the test suite does not exist yet; until it does, this rule means "write and run the test that should have existed.")

### 5.3 Emergency override procedure

Rules in §5.1 and §5.2 are hard rules. "Hard" means they require an explicit, authorized, documented decision to override — not that violations are physically impossible. A rule violated silently is not a rule; it is a suggestion nobody follows, which is more dangerous than having no rule at all because it creates the appearance of process without its substance.

**When a hard rule genuinely cannot be followed:**

1. **State the override explicitly.** Name the rule being broken and the specific reason. Do not proceed without stating this out loud.
   > *Example: "I am overriding rule §5.2.4 (diagnostics gate artifact writing). Reason: the user explicitly asked to inspect a system_prompt.md rendered from an agent that currently fails validation, to debug the FAQ block — this is the documented `compile_agent()` behavior, not actually an override, but flagging it since it looks unusual."*

2. **Request human authorization before acting.** The override does not happen until the decision-maker explicitly approves it (per §7). Silence is not approval.

3. **Log it in STATUS.yaml.** Add an entry to the `changelog`: rule overridden, reason, date. The override is not complete until it is logged.

4. **If the same rule is overridden twice:** the rule itself must be re-evaluated. Either it is too strict for this context (update the rule to reflect reality), or the situation that triggers the override is preventable (add a safeguard upstream). Do not continue overriding the same rule without addressing the root cause.

---

## 6. Autonomy Level

| Action type | Can the agent proceed alone? | Why? |
|---|---|---|
| Adding a unit test for existing behavior | Yes | Low risk, easy to revert, closes the test-coverage gap |
| Fixing a validator/renderer bug with a clear repro | Yes, propose scope first | Low-medium risk; still confirm scope before touching multiple files |
| Adding a new node type, DSL token, or compliance-checker hook | Agent proposes scope, human confirms before implementing | Changes the grammar every compiled agent depends on |
| Changing `app/classifier.py`'s split logic | Human always | Breaks the SSOT §10 guarantee that two agents from the same schema classify identically |
| Editing agent content in `agents/defs/` or `agents/shared/` (a separate repo) | Agent drafts, human confirms before any commit in that repo | Directly changes what a live medical bot says to real callers |
| Changing compliance profile severity (`profiles/compliance/*.yaml`) | Human always | Determines whether builds fail — a silent downgrade could ship non-compliant content |
| Deleting or rewriting `dist/` output | Yes | Fully regenerable from source, gitignored |
| Force-push, history rewrite, deleting the nested `agents/` repo | Never without explicit confirmation | Irreversible, affects a separately-owned repo |

---

## 7. What Counts as Approval

For the agent to advance a stage or execute an action that requires confirmation:

**Counts as approval:** "yes", "approved", "go ahead", "correct", "ok" as a direct response to a yes/no question.

**Does not count as approval:** "hmm", "interesting", "ok" without clear context, silence, an emoji. In case of ambiguity, ask again — never interpret as a green light.

---

## 8. Documentation: comments and docstrings

### 8.1 Inline comments

- No comments by default.
- Only when the WHY is non-obvious: a hidden constraint, a subtle invariant, a workaround for a specific bug, behaviour that would surprise a reader.
- No references to the current task, the fix, or callers — those belong in the PR description and rot as the codebase evolves.
- The existing codebase already documents non-obvious invariants inline (e.g. the SSOT §10 comment in `classifier.py`) — match that density, don't strip it and don't over-add.

### 8.2 Docstring format

Standard Python docstring conventions (this is not an ML/PyTorch project — no NumPy-style `Forward pass:` sections apply). Match the style already used in `app/`:

```python
def compile_agent(agent_dir: Path, params: CompilationParams) -> CompiledAgent:
    """
    One-line summary. Active present tense.

    Extended description if needed — side effects, ordering guarantees.

    Parameters:
        agent_dir (Path): Directory containing the agent's manifest.yaml and includes.
        params (CompilationParams): Frozen compilation configuration.

    Returns:
        CompiledAgent: Rendered artifacts plus validation/dedup/orphan reports.

    Raises:
        RuntimeError: If an alias reference is left unresolved after loading.
    """
```

### 8.3 Section rules

| Section | When required | Format |
|---|---|---|
| One-line summary | Always — first line of every docstring | Plain sentence |
| `Parameters:` | Any function/method with parameters | `name (type): desc.` |
| `Returns:` | Any callable with a non-trivial return | `type: desc.` |
| `Raises:` | When the function raises a specific, meaningful exception (e.g. unresolved alias, schema violation) | `ExceptionType: When condition.` |

### 8.4 What does NOT get a docstring

| Element | Reason |
|---|---|
| Private helpers (`_render_*`, `_resolve_path`, `_validate_no_unresolved_aliases`) | Self-documenting name; implementation detail of the public function that calls it |
| Trivial `@property` | Name is the documentation |
| Pydantic model fields already documented by their type + a `Field(description=...)` | Don't duplicate in a docstring |

### 8.5 Naming conventions

| Element | Convention | Example |
|---|---|---|
| Classes | `PascalCase` | `AgentSpec`, `SubflowTemplate` |
| Functions / methods | `snake_case` | `compile_agent`, `load_agent_spec` |
| Variables / attributes | `snake_case` | `agent_id`, `object_sources` |
| Constants | `UPPER_SNAKE_CASE` | `LOWER_SNAKE_RE`, `MAX_RETRY_ATTEMPTS` |
| Private module functions | `_leading_underscore` | `_resolve_path`, `_render_faqs` |
| Module files | `snake_case.py` | `mermaid_parser.py`, `build_prompt.py` |
| DSL identifiers (in YAML, not Python) | `lower_snake_case` for ids/slots/tools; `UPPER_SNAKE_CASE` for constants/state/handler/FAQ ids | `surrogate_questions_voice`, `MAX_RETRY_ATTEMPTS`, `X_ASK_AGE` |

---

*This file complements SPEC.md (specification), EXPERTS.md (review panel), FRAMEWORK.md (process), and TESTING.md (testing standards). Update when a rule changes permanently — not when a one-off implementation decision is made.*
