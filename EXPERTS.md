# EXPERTS.md — Expert Panel: Prompt Compiler

Panel of specialized roles that guide development. Each expert reviews from their domain before a change is considered complete. The goal: prevent each session from optimizing only one piece without seeing the whole system.

**Golden rule:** A change requires sign-off from the relevant experts. `@guardian` is always present.

---

## The Experts

---

### @guardian — Quality Guardian

**Always present in any review.**

**Domain:** Enforcement of CLAUDE.md policies, change scope, verifiable criteria.

**Responsibilities:**
- Verify that the change does not exceed the declared scope.
- Detect speculative abstractions: a new `CompilationParams` field, CLI flag, or schema field nobody asked for.
- Count the lines touched and ask whether it could be fewer.
- Verify that imports made unnecessary by the change were removed.
- Ensure that every changed line traces directly to an explicit requirement.
- Block changes that "improve" adjacent code nobody asked to touch (e.g. reformatting an unrelated `_render_*` helper while fixing a FAQ bug).
- Verify that verifiable success criteria were defined before implementing.

**The 5 questions of @guardian:**
```
1. Was this explicitly requested? (or assumed because "it would be useful")
2. Could it be solved with half the code?
3. Were files touched that didn't need to be touched?
4. Was it defined how to verify that it works?
5. Was any hard rule from CLAUDE.md §5 violated?
   If yes: was it stated explicitly before acting? Was human authorization obtained?
   Is it logged in STATUS.yaml? All three must be true — or it is a BLOCKER.
```

**Metrics:**
```
[ ] The diff contains no changes in files unrelated to the task
[ ] No new unused imports
[ ] No new functions/classes that are not called from any new location
[ ] New code has no optional parameters that nobody will use
[ ] At least one concrete verification criterion exists (a test, or a compiled fixture diff)
```

---

### @architect — System Architect

**Domain:** Compiler pipeline structure, the `app/` module hierarchy (SPEC.md §6), contracts between `schemas.py` → `loaders.py` → `validators.py`/`classifier.py` → `renderers.py` → `compiler.py` → entry points.

**Responsibilities:**
- Ensure the Level 0–5 dependency hierarchy (SPEC.md §6) is respected: `schemas.py`/`utils.py` never import upward.
- Verify that `compile_agent()` stays pure (no file I/O) and that file-writing responsibility stays in `build_prompt.py`/`main.py`.
- Approve new public interfaces (new `CompilationParams` fields, new `app/` module boundaries) and their contracts.
- Ensure a new node type, DSL construct, or CLI subcommand doesn't require rewriting `compiler.py`'s orchestration order — it should slot into an existing stage.
- Verify that `classifier.py`'s SSOT §10 split logic is not made configurable per agent.

**Metrics:**
```
[ ] The Level 0-5 dependency hierarchy has no upward imports
[ ] compile_agent() performs no filesystem writes
[ ] The contract (inputs/outputs/guarantees) of each touched component is documented in SPEC.md §9 if it changed
[ ] Changes to public interfaces are justified: breaking changes explicitly declared
[ ] classifier.py's split logic remains non-configurable per agent (SSOT §10)
[ ] A new DSL construct doesn't require reordering compile_agent()'s 7 stages
```

---

### @dsl-expert — DSL & Compiler Correctness Specialist

**Alias:** `@dsl-expert`

**Domain:** The agent-definition DSL itself — `app/schemas.py` grammar, `app/utils.py` token regexes (`VAR_RE`, `CONST_RE`, `SLOT_RE`, `GOTO_RE`, `PARAM_RE`, alias regexes), `app/loaders.py` merging/namespacing/alias-resolution, `app/validators.py` graph checks, `app/renderers.py` + `templates/*.j2` output fidelity, and Mermaid round-trip tooling (`mermaid_parser.py`, `mermaid_diagrams.py`).

**Responsibilities:**
- Verify that a DSL grammar change is reflected consistently across all four coupled surfaces: `schemas.py` (structural validator), `loaders.py` (if it affects merging/namespacing), `renderers.py` + the relevant `templates/*.j2` (output), and `AGENT_CREATION_GUIDE.md`/`PATTERN_GUIDE.md` (docs). A change to only one of these is incomplete.
- Review that per-node-type invariants (`question` requires `wait='yes'` + non-empty `say`; `decision` must not have `say`; `action` must declare `execute`; `terminal` must have `final='yes'`) are preserved or deliberately, visibly changed.
- Confirm naming-regex enforcement (`LOWER_SNAKE_RE`, `UPPER_CONST_RE`, `UPPER_ID_RE`, `RULE_ID_RE`) is not weakened or bypassed.
- Confirm subflow namespacing (`NAMESPACE__STATE_ID`, `namespace__slot`, double underscore) stays consistent, and that `_validate_no_unresolved_aliases` still raises (not warns) on any leftover `@instance.export` reference.
- Verify retry-counter convention (`<slot>_retry_count` capped by `<MAX_RETRY_ATTEMPTS>`) is honored for any new self-looping `question` state, per `PATTERN_GUIDE.md`.
- Approve changes to the Jinja2 `[[ ]]` delimiter convention or the block names `build_render_context()` assembles (`system_constants_block`, `handlers_block`, `faqs_block`, etc.) — these are the template/renderer contract.

**Metrics:**
```
[ ] A schema/grammar change updates schemas.py, loaders.py (if needed), renderers.py + templates/*.j2,
    and AGENT_CREATION_GUIDE.md together — not a subset
[ ] Node-type structural invariants (question/decision/action/terminal) still enforced by a model_validator
[ ] Naming regexes (lower_snake_case ids, UPPER_SNAKE_CASE constants/states, RULE_ID_RE) unchanged or
    changed with explicit justification
[ ] Subflow namespacing still uses double underscore; unresolved aliases still raise, not warn
[ ] Self-looping question states have a retry counter capped by a MAX_RETRY_ATTEMPTS-style constant
[ ] Compilation remains deterministic (no unordered dict iteration leaking into rendered output)
```

---

### @compliance-expert — Medical/Legal Compliance Specialist

**Alias:** `@compliance-expert`

**Domain:** Colombian medical/data-privacy compliance content — `profiles/compliance/medical_es.yaml`, `say_verbatim` usage, legal-citation constants (e.g. `DATA_LAW_REFERENCE`), and any FAQ/handler content touching consent, data privacy (Ley 1266/2008, Ley 1581/2012, Decreto 886/2014), medical-emergency escalation, or pricing/contract language.

**Responsibilities:**
- Verify that legally- or medically-sensitive content is marked `say_verbatim: true` rather than left paraphrasable.
- Ensure no legal citation, compliance rule, or clinical claim is invented or paraphrased from memory — it must trace to source material already in the repo or explicitly supplied by the user (CLAUDE.md §5.1.1).
- Review that compliance rule changes in `profiles/compliance/*.yaml` (adding a rule, changing severity error↔warning) are flagged for human sign-off, never silently landed.
- Confirm topics flagged in `faq.md`/`handlers.md` as requiring "official content" (compensation amount, legal contract terms, specific medical risks) are not answered with placeholder or inferred text in shipped agent definitions.
- Check that no real PII (names, phone numbers, cédula numbers, medical history) appears anywhere in the diff, including examples and test fixtures.

**Metrics:**
```
[ ] Legally/medically sensitive say content is marked say_verbatim: true
[ ] No invented legal citation or clinical claim — sourced from repo material or the user, not inferred
[ ] Compliance profile severity changes (error <-> warning) are explicitly flagged for human approval
[ ] "Requires official content" topics (compensation, contract, medical risk) are not answered with
    placeholder/inferred text
[ ] No real PII anywhere in the diff (fixtures, tests, docs, examples)
```

---

### @qa — QA and Testing Specialist

**Domain:** Test suite, acceptance criteria, coverage, regressions. Currently the highest-priority expert given the project has zero automated tests (SPEC.md §5, §10 roadmap phase 15-16).

**Responsibilities:**
- Verify that every new/changed validator, loader behavior, or renderer output has at least one test that verifies its behavior in the base case.
- Ensure tests use synthetic fixture YAML (never real content from the separate `agents/` repo) generated within `tests/` or via `tmp_path`.
- Review that tests run without needing the `agents/` nested repo, network, or GPU.
- Verify that a bug-fix test fails without the fix (test-first verified) when practical.
- Ensure that no existing test was disabled without documented justification.

**Metrics:**
```
[ ] The complete test suite (once it exists) passes without errors locally
[ ] No existing test was deleted or disabled without documented justification
[ ] New tests use synthetic fixture YAML, not real agents/defs/ or agents/shared/ content
[ ] Tests do not depend on the nested agents/ repo being present or network access
[ ] New validator: test with a synthetic AgentSpec that violates the rule + one that satisfies it
[ ] New renderer output: test asserts exact expected Markdown/JSON fragment, not just "no exception"
[ ] Mermaid round-trip: parse→scaffold and AgentSpec→diagram each have at least one fixture test
```

---

### @security — Security Specialist

**Domain:** Vulnerabilities introduced or exposed by the change: secrets, insecure deserialization, injection, path traversal, sensitive data in logs, prompt-injection surface in generated prompts, vulnerable dependencies.

**Responsibilities:**
- Review the diff with an attacker mindset: what can someone exploit who controls YAML input, a Mermaid diagram file, or the execution environment.
- Verify there are no hardcoded secrets or versioned credentials.
- Detect insecure YAML/deserialization handling (`yaml.load` without a safe loader, `eval`/`exec` on external data).
- Ensure path construction from agent ids / file references (`loaders._resolve_path`) is normalized and cannot escape `agents/defs/` or `agents/shared/` via a crafted `agent_id` or include path.
- Verify that compiled prompt output cannot be manipulated by attacker-controlled YAML content to inject instructions outside its intended DSL role (prompt-injection-by-content risk, given the output is itself an LLM system prompt).
- Ensure no sensitive data (real PII) leaks into logs, error messages, or generated reports.

**Metrics:**
```
[ ] No hardcoded secrets in the diff
[ ] YAML loaded via yaml.safe_load (or equivalent), never yaml.load with a permissive loader
[ ] No eval/exec on YAML-sourced strings
[ ] Path resolution for agent_id / include paths is normalized and cannot traverse outside
    agents/defs/ or agents/shared/
[ ] Free-text YAML fields (say, notes) cannot inject content that breaks out of the DSL's
    fenced block structure in the rendered prompt
[ ] No PII in logs, error messages, or diagnostic reports
[ ] New dependencies without known CVE
```

---

### @api — Public API Designer

**Domain:** CLI surface (`build_prompt.py`, `scaffold_from_mermaid.py`), TUI (`main.py`), and the `compile_agent()` / `load_agent_spec()` / `validate_agent_spec()` function signatures that external callers (or future tooling) depend on.

**Responsibilities:**
- Verify that public functions have type annotations and docstrings with `Parameters:`/`Returns:`/`Raises:` (CLAUDE.md §8).
- Review that CLI flags and TUI menu options are self-explanatory and consistent with existing naming (`--channel`, `--verbosity`, `--fail-on-warnings`-style conventions).
- Ensure error messages from loaders/validators are informative: which file, which field, what was expected.
- Approve changes to `CompilationParams` or `AgentSpec` — they are the contract between pipeline stages and any external caller.
- Verify that reference-asset JSON output remains structurally stable (or a schema-version bump is called out) since it's meant for RAG ingestion by an external system.

**Metrics:**
```
[ ] All public parameters have type annotations
[ ] All public functions/methods have docstrings with Parameters/Returns/Raises
[ ] Error messages include: file/field context, received value, expected value or valid range
[ ] Changes to CompilationParams or AgentSpec document whether they are breaking changes
[ ] reference_asset.json structural changes are called out explicitly (consumed by an external RAG system)
```

---

### @build-pipeline — Build Pipeline Specialist

**Domain:** The compile-time lifecycle guarantees of `compile_agent()`'s 7 stages, diagnostic-report behavior, and CLI/TUI execution flow. (Adapted from the generic "Operations" role — this project has a build pipeline, not a training/inference loop.)

**Responsibilities:**
- Verify the pipeline order (Load → Classify → Validate → Deduplicate → Detect orphans → Render) is preserved, and any new stage has an explicit, justified insertion point.
- Ensure diagnostic reports (validation/deduplication/orphan) are always written, regardless of outcome.
- Verify compiled artifacts (`system_prompt.md`, `reference_asset.*`) are only written on a clean validation pass, unless an explicit override flag is used and surfaced to the user.
- Confirm compilation stays deterministic — no run-to-run output drift from dict ordering, timestamps, or filesystem iteration order.
- Review that `build_prompt.py` and `main.py` stay thin wrappers over `compile_agent()` rather than duplicating pipeline logic.

**Metrics:**
```
[ ] compile_agent()'s stage order is unchanged, or a new stage's insertion point is explicitly justified
[ ] Diagnostic reports are written unconditionally
[ ] Compiled artifacts are gated on validation success (or an explicit, surfaced override)
[ ] Two consecutive compiles of the same source produce byte-identical output
[ ] CLI/TUI entry points do not reimplement logic that belongs in compiler.py
```

---

## Work Dynamics

### When each expert is activated

| Change type | Required experts | Context level |
|---|---|---|
| Bugfix in an existing `_render_*` / `_validate_*` helper, no grammar change | @guardian @qa | LOW |
| New validator or compliance checker | @dsl-expert @qa @guardian | MEDIUM |
| New DSL construct / node type / token syntax | @architect @dsl-expert @api @qa @guardian | HIGH |
| Change to `app/classifier.py` split logic | @architect @dsl-expert @guardian | HIGH — requires human decision per SPEC.md §7 |
| Change to `profiles/compliance/*.yaml` (new rule or severity change) | @compliance-expert @qa @guardian | HIGH |
| Change to `templates/*.j2` (System Prompt output text/instructions) | @dsl-expert @compliance-expert @qa @guardian | HIGH |
| Change to `agents/defs/` or `agents/shared/` content (separate nested repo) | @compliance-expert @guardian | MEDIUM — content, not compiler code |
| Change to `compiler.py` orchestration or pipeline stage order | @architect @build-pipeline @qa @guardian | HIGH |
| Change to `build_prompt.py` CLI flags or `main.py` TUI | @api @build-pipeline @guardian | MEDIUM |
| Mermaid parser/exporter change | @dsl-expert @qa @guardian | MEDIUM |
| New dependency, YAML/file I/O, or path handling | @security @guardian | HIGH |
| Adding tests to close the current coverage gap | @qa @guardian | LOW |
| Documentation-only change (`AGENT_CREATION_GUIDE.md`, `PATTERN_GUIDE.md`, etc.) | @guardian | LOW |

---

### Process before implementing

Before writing code, declare:

```
Task: [description of the expected outcome, not just the output]
Scope: [files to be touched / files NOT to be touched]
Success criteria:
  - [verifiable criterion 1 — a test, an assertion on rendered output, a validator report diff]
  - [verifiable criterion 2]
Required experts: [@guardian + those that apply per the table above]
```

If verifiable criteria cannot be defined, clarification is requested before continuing. Implementation does not start with vague criteria.

---

### Metric derivation process for a specific change

Each time a change is proposed, the relevant experts derive concrete metrics from the specific change. Example:

> **Proposed change:** Add a new `SLOT_ALIAS_RE`-based validator that flags a slot alias referencing an export the target subflow never declares.
>
> **@architect derives:**
> - The new check slots into the existing `validate_agent_spec()` call sequence without reordering earlier checks.
>
> **@dsl-expert derives:**
> - The check correctly distinguishes a *declared-but-unused* export (fine) from a *referenced-but-undeclared* export (error).
> - Namespacing (`namespace__slot`) is respected when resolving the alias target.
>
> **@qa derives:**
> - Test 1: synthetic `AgentSpec` with a slot alias pointing at a real export → no error.
> - Test 2: synthetic `AgentSpec` with a slot alias pointing at a nonexistent export → validator reports it with the correct location.

---

### Conflict resolution between experts

1. **@guardian always has veto** over unnecessary complexity.
2. If @architect and @dsl-expert differ on where a new check belongs (loader-time vs. validator-time), the trade-off is documented in `SPEC.md §13` before implementing.
3. If @api and @architect differ on the exposure of a new `CompilationParams` field, the more restrictive option (less public surface) wins until there is a concrete use case.
4. If @compliance-expert and @dsl-expert disagree on whether content should be `say_verbatim`, @compliance-expert's judgment wins by default (compliance risk outweighs authoring convenience) — document the disagreement in `SPEC.md §13` regardless.
5. The conflict documented in `SPEC.md §13` is information for the decision-maker — it is not averaged into a consensus response.

---

## Quick review template

Use before declaring a change as complete:

```
## Review: [change name]
Date: YYYY-MM-DD

### @guardian (always)
[ ] Correct scope (does not exceed what was requested)
[ ] Minimum necessary code
[ ] Only relevant files were touched
[ ] Verification criterion defined and executed: ___

### @architect (if applicable)
[ ] Level 0-5 dependency hierarchy respected (SPEC.md §6)
[ ] compile_agent() still performs no filesystem writes
[ ] Component contract documented (SPEC.md §9) if changed
[ ] No undeclared breaking changes to CompilationParams/AgentSpec

### @dsl-expert (if applicable)
[ ] Grammar change reflected in schemas.py + loaders.py (if needed) + renderers.py/templates + docs
[ ] Node-type structural invariants preserved or deliberately changed
[ ] Naming regexes and namespacing conventions unchanged or justified
[ ] Compilation remains deterministic

### @compliance-expert (if applicable)
[ ] say_verbatim used correctly for legally/medically sensitive content
[ ] No invented legal/clinical claims
[ ] Compliance severity changes flagged for human sign-off
[ ] No real PII in the diff

### @build-pipeline (if applicable)
[ ] Pipeline stage order preserved or justified
[ ] Diagnostic reports still written unconditionally
[ ] Artifact writing still gated on validation

### @api (if applicable)
[ ] Types annotated on all public parameters
[ ] Complete docstrings (Parameters + Returns + Raises)
[ ] Informative error messages (file/field, received, expected)
[ ] reference_asset.json structural stability preserved or version-bumped

### @security (if applicable — I/O, YAML parsing, paths, dependencies)
[ ] No hardcoded secrets in the diff
[ ] Safe YAML loading only
[ ] No path traversal via agent_id / include paths
[ ] No prompt-injection surface introduced into rendered output
[ ] No PII in logs or error messages
[ ] New dependencies without known CVE

### @qa (if applicable)
[ ] Test of base case + at least one edge case
[ ] Tests use synthetic fixture YAML, not real agents/ content
[ ] Tests run without the nested agents/ repo or network access
[ ] No existing test was deleted or disabled
```

---

*This document complements CLAUDE.md (policies) and SPEC.md (specification). Update when new technical domains are added to the project, when the most frequent change types change, or when recurring error patterns appear that justify a new role.*
