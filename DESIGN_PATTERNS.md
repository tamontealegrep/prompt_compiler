# DESIGN_PATTERNS.md — Pattern Catalog: Prompt Compiler

> Version: 2.0.0 | Date: 2026-08-06
> Living registry of proven design patterns and anti-patterns to avoid in Prompt Compiler. Updated when a pattern is confirmed as valid across more than one change, or when an anti-pattern causes a real problem. This is not a theoretical reference — it is evidence of the system working.

---

## How to use this document

- **When designing:** check whether the problem you are trying to solve already has a documented pattern. If it does, apply it. If not, design first, then document.
- **When reviewing:** use the Anti-patterns section as an additional checklist for @guardian.
- **When updating:** when a new pattern appears in two separate changes, add it here. When an anti-pattern causes a real problem in the project, add it to the catalog.

---

## Pattern structure

```
### [Pattern name]
**Category:** [Architectural / Operational / Data / Process / API]
**Problem:** [One sentence: what problem it solves]
**Solution:** [Concrete description of the solution]
**When to apply:** [The conditions that trigger this pattern]
**When NOT to apply:** [The conditions that make this pattern incorrect]
**Example:** [Concrete example from the project]
**Trade-offs:** [What is gained and what is given up by applying this pattern]
**See also:** [Other related patterns]
```

---

## System patterns

### P01 — Frozen compilation params, mutable AgentSpec assembly

**Category:** Architectural
**Problem:** A compilation run needs a stable, hashable configuration (channel, verbosity, compliance profile) while the in-memory agent representation is being progressively assembled from many YAML files.
**Solution:** `CompilationParams` is a frozen dataclass — it cannot change mid-pipeline. `AgentSpec` is built up by `loaders.py` across multiple merge/resolve passes and only becomes "final" once `_validate_no_unresolved_aliases` passes.
**When to apply:** Any time a pipeline has a small, fixed set of run-level knobs and a large, progressively-assembled intermediate representation.
**When NOT to apply:** Don't make `AgentSpec` itself frozen — it needs to accumulate namespaced states/slots across subflow instantiation passes before validation.
**Example:**
```python
# Good: params never change during the run
params = CompilationParams(channel="voice", verbosity="standard", ...)
spec = load_agent_spec(agent_dir, params, channel_profile)  # spec is assembled, params is not
```
**Trade-offs:** Requires threading `params` explicitly through every stage instead of mutating shared state — more verbose call signatures, but each stage's behavior is fully determined by its explicit inputs.
**See also:** P08 (Pipeline stages never write files except the orchestrator's caller)

---

### P02 — Fixed content classification (SSOT §10)

**Category:** Architectural
**Problem:** If the rule deciding whether a field belongs in the System Prompt or the Reference Asset were configurable, two agents built from structurally identical source data could diverge in what the LLM actually sees at runtime vs. what only lives in retrieval — an invisible, hard-to-debug divergence.
**Solution:** `app/classifier.py` implements one fixed, stateless rule. No `CompilationParams` field, CLI flag, or per-agent YAML setting is allowed to alter it.
**When to apply:** Any splitting/routing decision where consistency *across* agents matters more than per-agent flexibility.
**When NOT to apply:** Decisions that are genuinely agent-specific (e.g. `channel` selection, `verbosity`) — those are legitimately configurable and belong in `CompilationParams`.
**Example:**
```python
# Good: classify() takes only the spec, no config
def classify(spec: AgentSpec) -> ClassifiedContent: ...

# Bad: a hypothetical config knob that would break the guarantee
def classify(spec: AgentSpec, split_mode: str) -> ClassifiedContent: ...  # never do this
```
**Trade-offs:** An agent author who wants different classification for a specific field has no escape hatch — by design. A genuine new need here is a schema-level decision (SPEC.md §7), not a config parameter.
**See also:** A10 (Configurable classification — anti-pattern)

---

### P03 — Namespacing with a mandatory double underscore

**Category:** Architectural / Data
**Problem:** Instantiating the same `SubflowTemplate` more than once (or across agents sharing `agents/shared/subflows/`) risks state-id and slot-name collisions if instances aren't disambiguated.
**Solution:** Every subflow instance's state ids and slot names are namespaced with the instance name, using a double underscore separator: `NAMESPACE__STATE_ID` (upper, for states) and `namespace__slot` (lower, for slots). This separator is never used elsewhere in identifier construction, so it can be split on unambiguously.
**When to apply:** Any reusable, instantiable template construct (subflows) that can appear more than once in a compiled agent.
**When NOT to apply:** Top-level, non-instantiated identifiers (regular states, global handlers) — those keep plain `UPPER_SNAKE_CASE`/`lower_snake_case` with no namespace prefix.
**Example:**
```
SubflowTemplate "callback" instantiated as "reschedule_callback"
  → state CALLBACK_ASK_TIME becomes RESCHEDULE_CALLBACK__CALLBACK_ASK_TIME
  → slot callback_time becomes reschedule_callback__callback_time
```
**Trade-offs:** Longer identifiers in compiled output. In return: two instances of the same subflow template can never collide, and the separator makes namespace boundaries visually and programmatically unambiguous.
**See also:** P04 (Alias resolution must be exhaustive)

---

### P04 — Alias resolution must be exhaustive, or fail loudly

**Category:** Architectural
**Problem:** A `GO_TO: @instance.export` or `@slot(instance.export)` reference that survives loading unresolved would silently compile into a broken or nonsensical target in the rendered prompt — a failure mode invisible until a live call hits it.
**Solution:** After all merge/resolve passes, `loaders.py` runs `_validate_no_unresolved_aliases` as a hard guard that raises `RuntimeError` if *any* alias token remains unresolved anywhere in the spec.
**When to apply:** Any multi-pass resolution process (aliases, template parameter substitution, cross-references) where "silently pass through unresolved" is a worse failure mode than "crash loudly at build time."
**When NOT to apply:** Genuinely optional references where "absent" is valid input, not an error.
**Example:**
```python
# Good: exhaustive check after all resolution passes
_validate_no_unresolved_aliases(spec)  # raises RuntimeError if anything survives

# Bad: a hypothetical soft warning that lets a broken reference through to rendering
_warn_unresolved_aliases(spec)  # never do this for GO_TO / export references
```
**Trade-offs:** A single unresolved reference blocks the entire compile (no partial output for that concern). In return: a broken cross-reference can never silently reach a compiled prompt.
**See also:** P03 (Namespacing), A11 (Soft-failing a hard reference)

---

### P05 — Compliance is data, not code

**Category:** Architectural / Data
**Problem:** Compliance rules and their severity change based on evolving legal/medical review — hardcoding them means every severity change requires a code review and a deploy cycle, which is both slow and puts compliance judgment in the hands of whoever reviews Python diffs.
**Solution:** `profiles/compliance/*.yaml` (e.g. `medical_es.yaml`) declares rules and severities. `app/validators.py` hosts a `_COMPLIANCE_CHECKERS` registry of checker *functions*; the registry is data-driven — adding a rule that reuses an existing checker is a pure YAML edit.
**When to apply:** Any rule set whose *content* changes independently of the *mechanism* that evaluates it, especially when non-engineers (compliance/legal reviewers) need to be able to propose a severity change.
**When NOT to apply:** Rules that require genuinely new evaluation logic still need a new Python checker function — the registry doesn't eliminate code changes, it eliminates them for severity/applicability tuning.
**Example:**
```yaml
# profiles/compliance/medical_es.yaml
rules:
  - id: "PII_001"
    checker: "no_raw_cedula_in_say"
    severity: "error"
```
**Trade-offs:** Two places to look (YAML + registry) to understand a given rule's full behavior. In return: a severity change ships without a Python code review, and is auditable as a YAML diff.
**See also:** A12 (Hardcoded compliance severity — anti-pattern)

---

### P06 — Propose and wait

**Category:** Process
**Problem:** An agent that executes and then reports generates irreversible actions without confirmation, especially when the change affects a live medical bot's compiled output or has legal/compliance consequences.
**Solution:** For any DSL grammar change, compliance-profile severity change, or edit to `agents/defs/`/`agents/shared/` content: the agent proposes the action with enough detail to evaluate it, and waits for explicit confirmation before executing.
**When to apply:** DSL grammar changes, compliance-profile edits, any change to agent content in the separate `agents/` repo.
**When NOT to apply:** Internal drafts, read-only exploration, adding a test for already-shipped behavior.
**Example:**
```
Agent: "I am going to mark the pricing-disclosure FAQ answer as say_verbatim: true
in agents/shared/faqs/pricing.yaml, since it currently allows paraphrasing of a
figure that should be exact. Do you approve this change, or is there a reason it
was left flexible?"
```
**Trade-offs:** Adds latency to the process. In return: eliminates the entire category of "the agent silently changed what a caller hears."
**See also:** P09 (Verifiable definition of done)

---

### P07 — Diagnostics always written, artifacts conditionally written

**Category:** Operational
**Problem:** If compiled artifacts and diagnostic reports shared the same gate, a failed build would leave an author with no information about *why* it failed — just a missing `system_prompt.md`.
**Solution:** `compile_agent()` always renders and always produces `validation_report.md` / `deduplication_report.md` / `orphan_states_report.md`. `system_prompt.md` / `reference_asset.*` are only written to `dist/` when validation passes cleanly (or an explicit override is used).
**When to apply:** Any build/compile pipeline where debuggability of a failed run matters as much as the success path.
**When NOT to apply:** Pipelines where a partial/invalid artifact could itself be dangerous if accidentally picked up downstream — not the case here, since `dist/` writes are explicitly gated.
**Example:** See SPEC.md §8 invariant 2 and CLAUDE.md §5.2.3-4.
**Trade-offs:** Requires keeping "always run" and "conditionally persist" as two separate concerns in `compiler.py` — slightly more orchestration code than a single pass/fail gate. In return: every failed build is debuggable from its reports alone.
**See also:** P01 (Frozen params / mutable assembly)

---

### P08 — The compiler stays pure; the entry point writes files

**Category:** Architectural
**Problem:** A compiler function that both computes output and writes files is hard to test (needs a real or mocked filesystem for every test) and conflates "did compilation succeed" with "did the write succeed."
**Solution:** `compile_agent()` returns rendered content and reports in memory; only `build_prompt.py` (and `main.py`'s TUI) decide whether/where to write to `dist/`.
**When to apply:** Any core computation that has an obvious, separable "persist the result" step.
**When NOT to apply:** N/A within this project — this separation is a hard invariant (SPEC.md §8.1), not a situational choice.
**Example:**
```python
# Good
result = compile_agent(agent_dir, params)   # pure, in-memory
if not result.validation_report.has_errors:
    write_dist_output(agent_dir.name, result)  # build_prompt.py's job

# Bad — never do this
def compile_agent(agent_dir, params, dist_dir):
    ...
    (dist_dir / "system_prompt.md").write_text(...)  # violates the invariant
```
**Trade-offs:** None meaningful here — this is a pure win for testability given the project's actual needs.
**See also:** P07 (Diagnostics always written)

---

### P09 — Verifiable definition of done

**Category:** Process
**Problem:** Without concrete completion criteria, "done" means "I have no more ideas to add" — not "it meets what was asked for."
**Solution:** Before implementing any change, define at least one verifiable criterion: a test that passes, a validator report that flags (or stops flagging) a specific fixture, an exact rendered-output fragment. At the end, execute the criteria — do not assume them.
**When to apply:** Every change, without exception.
**When NOT to apply:** No exception — even "update AGENT_CREATION_GUIDE.md" has a verifiable criterion ("the described YAML shape in the guide matches what `schemas.py` actually accepts").
**Example:**
```
Success criteria for a new "must confirm before terminal" node invariant:
[ ] AgentSpec construction raises ValidationError when a `terminal` node lacks `final='yes'`
    (already true — regression-guard this)
[ ] tests/unit/app/test_schemas.py::test_terminal_node_requires_final_yes passes
[ ] AGENT_CREATION_GUIDE.md's terminal-node section reflects the same rule
```
**Trade-offs:** Adds design time before implementation. In return: eliminates ambiguity about what "ready" means.
**See also:** P06 (Propose and wait)

---

### P10 — Isolated perspectives for high-impact decisions

**Category:** Process
**Problem:** Asking a single agent to evaluate "from all angles" produces an averaged opinion that dilutes real disagreement — especially dangerous here, where `@dsl-expert` (authoring convenience) and `@compliance-expert` (legal/medical risk) can have genuinely opposed defaults.
**Solution:** For high-impact decisions, assign separate criteria to separate roles (EXPERTS.md). Each role reviews with its own metrics. Conflict between roles is documented as such, not resolved by the reviewing agent.
**When to apply:** DSL grammar changes, compliance-profile changes, changes touching `say_verbatim` defaults.
**When NOT to apply:** Small changes with a single affected domain (only `@guardian` is sufficient).
**Trade-offs:** Requires more review time. In return: real conflicts (e.g. "should this be flexible or verbatim") surface before they reach a compiled, shippable prompt.
**See also:** P09 (Verifiable definition of done)

---

### P11 — The process lives in the system (not in someone's memory)

**Category:** Process
**Problem:** A process that only exists in someone's head is a process that disappears when that person is unavailable, or when the session ends.
**Solution:** Any process that matters must be written in a place that is consulted at the time of working, not in an archived document written once and forgotten. The seven system files (CLAUDE.md, SPEC.md, EXPERTS.md, FRAMEWORK.md, TESTING.md, STATUS.yaml, DESIGN_PATTERNS.md) are that place — alongside the domain-authoring docs (`AGENT_CREATION_GUIDE.md`, `PATTERN_GUIDE.md`) that already existed before this framework was adopted.
**When to apply:** Any rule, decision, or criterion that must be applied consistently across sessions.
**When NOT to apply:** One-off decisions from a single session that have no future impact.
**Signal that it is not being applied:** "I already know how the DSL should behave here", "I'll document it later."
**Trade-offs:** Requires discipline to update the files. In return: project knowledge (including hard-won DSL edge cases) is transferable and auditable.
**See also:** P05 (Chronological changelog — STATUS.yaml)

---

## Anti-patterns

### A01 — The monolithic prompt (development-process sense)

**Description:** A single task/prompt that simultaneously tries to resolve pipeline (what order), memory (what was decided before), and review (whether it is done correctly).
**Why it fails:** None of the three dimensions receives the attention it needs.
**Signal:** "Add the new node type, and also verify it's correct" as one undivided step.
**Solution:** Separate: first declare the task (FRAMEWORK.md §3), then implement, then review with the panel (EXPERTS.md).

---

### A02 — The partial DSL change

**Description:** Editing `schemas.py` to add a field or node-type rule without updating the corresponding `renderers.py`/`templates/*.j2` output, or without updating `AGENT_CREATION_GUIDE.md`/`PATTERN_GUIDE.md`.
**Why it fails:** The schema now accepts something the renderer doesn't know how to emit correctly (or emits inconsistently with what the docs promise), and an agent author following the guide writes YAML that either doesn't validate or compiles to something unexpected.
**Signal:** A diff that touches `schemas.py` but not `renderers.py`/`templates/` for a genuinely new construct, or vice versa.
**Solution:** CLAUDE.md §5.2.7 and FRAMEWORK.md §5.9 — a DSL grammar change ships across schema, loader (if relevant), renderer/template, and docs together.

---

### A03 — Configurable classification (breaking SSOT §10)

**Description:** Adding a parameter that lets classification of a field (System Prompt vs. Reference Asset) vary per agent or per call.
**Why it fails:** Breaks the guarantee that structurally identical source data always classifies identically — makes compiled agents silently diverge in what an LLM actually sees vs. what's only retrievable.
**Signal:** A new `CompilationParams` field, or a per-agent YAML key, that `classifier.py` branches on.
**Solution:** P02 (Fixed content classification). If a genuine new need arises, it's a SPEC.md §7/§12 decision, not a config parameter.

---

### A04 — Soft-failing a hard reference

**Description:** Turning `_validate_no_unresolved_aliases` (or an equivalent exhaustive-resolution guard) into a warning instead of a `RuntimeError`.
**Why it fails:** A dangling `GO_TO`/`@instance.export` reference then silently reaches the rendered prompt, and only surfaces when a live call hits the broken transition.
**Signal:** A change that replaces `raise RuntimeError(...)` with a log/warning for an alias-resolution failure.
**Solution:** P04 (Alias resolution must be exhaustive, or fail loudly). Hard references stay hard failures.

---

### A05 — Hardcoded compliance severity

**Description:** Adding an `if rule_id == "PII_001": severity = "error"`-style branch directly in `validators.py` instead of reading severity from `profiles/compliance/*.yaml`.
**Why it fails:** Severity changes then require a Python code review and deploy instead of a YAML edit — slower, and puts a compliance judgment call inside a code diff where it's easy to miss.
**Signal:** A compliance-severity literal appearing in `.py` code outside the registry dispatch mechanism.
**Solution:** P05 (Compliance is data, not code).

---

### A06 — The demo that works

**Description:** A DSL change or bug fix that compiles one hand-checked agent correctly, with no formal test.
**Why it fails:** The next unrelated change can silently break the same case, and nobody will know until a real compile (or worse, a real call) fails.
**Signal:** "I compiled it manually and the output looked right" with no test added.
**Solution:** P09 (Verifiable definition of done). Always at least one written and executed test.

---

### A07 — Mistaking historical/informal docs for the live spec

**Description:** Treating `gestantes_old.md` (a pre-`HARD_TOOL_EXECUTION_CONTRACT` draft prompt) or `faq.md`/`handlers.md` (informal consulting-style recommendations) as authoritative current behavior.
**Why it fails:** `gestantes_old.md` predates a documented template addition and no longer reflects the live DSL; `faq.md`/`handlers.md` are recommendations for content, not schema documentation — `AGENT_CREATION_GUIDE.md` and `PATTERN_GUIDE.md` are the authoritative technical references.
**Signal:** A design decision justified by "that's what `gestantes_old.md` does" instead of the current `templates/*.j2`/`schemas.py`.
**Solution:** Always check `AGENT_CREATION_GUIDE.md` + the actual `app/` source for current behavior; treat the older/informal docs as historical or advisory context only (SPEC.md §5 known technical debt, §12 open question 1).

---

### A08 — The surgical change that expands

**Description:** Starting to fix a validator bug and ending up reformatting `renderers.py` or renaming unrelated helpers because "I was already in that file."
**Why it fails:** The declared scope no longer matches the actual diff, making it harder to reason about what actually changed and why, especially for a diff that could affect a compiled medical bot's output.
**Signal:** The diff contains changes in files not in the declared scope (FRAMEWORK.md §3).
**Solution:** CLAUDE.md §3 (Surgical changes). Mention what "should be improved" without touching it; open it as a separate task if it's worth doing.

---

### A09 — The oral decision

**Description:** A DSL or compliance-severity decision is made in conversation but never written to `SPEC.md §13`. The next session has no context and re-debates or re-decides it differently.
**Why it fails:** Unwritten decisions get re-made inconsistently, or discovered as "an implicit decision" only after something breaks.
**Signal:** "But we'd already decided `say_verbatim` should default to true for pricing content, right?" appearing in two different sessions.
**Solution:** FRAMEWORK.md §8 (session-close checklist). Any non-trivial decision goes in `SPEC.md §13` before closing the session.

---

### A10 — The retrospective test

**Description:** A test written *after* a validator/renderer change to document what the code now does — not to define what it should do.
**Why it fails:** A test that describes existing code can only catch bugs introduced *after* the test was written, not the one that prompted writing it. Given this project currently has zero tests (SPEC.md §5), the temptation to backfill tests that just describe current output — including current bugs — is real.
**Signal:** The test passes on the first run without any change to the code under test. The phrase "I'll add the test after, just to lock in the current behavior."
**Solution:** RED→GREEN→REFACTOR (FRAMEWORK.md §2, Pillar 1). Write the test to describe the *correct* behavior first — for existing untested code, this means writing the test against what the code *should* do, running it, and confirming any failure is a real bug, not writing it to match whatever the code currently emits.
**See also:** P09 (Verifiable definition of done)

---

## How this catalog evolves

A pattern is **added** when:
- It appeared organically in at least two separate changes to the project.
- It solved a real problem that would have been solved worse or taken longer without the pattern.

An anti-pattern is **added** when:
- It caused a bug, rework, or a decision that had to be undone.
- It was recognized as "this has happened before" on more than one occasion.

A pattern or anti-pattern is **updated** when:
- The project evolved and the pattern no longer applies in the same way.
- A legitimate exception appeared that is worth documenting.

A pattern is **removed** when:
- It no longer applies to the project in its current state.
- It was discovered that the pattern causes more problems than it solves.

---

*This document complements FRAMEWORK.md (process), SPEC.md §13 (decision log), and EXPERTS.md (review criteria). Update when new patterns appear — not only when convenient.*
