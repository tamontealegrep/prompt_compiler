# Prompt Compiler — Master System Specification

**Version:** 1.0.0 | **Date:** 2026-08-06 | **Status:** Active — core pipeline stable, no automated tests yet
**Audience:** Development team, continuous development LLM agents
**Purpose:** Single Source of Truth for the specification, design, and continuation of development

---

## Table of contents

1. [Executive summary](#1-executive-summary)
2. [System objective](#2-system-objective)
3. [Scope](#3-scope)
4. [Non-objectives](#4-non-objectives)
5. [Current status](#5-current-status)
6. [Target architecture](#6-target-architecture)
7. [Architectural decisions](#7-architectural-decisions)
8. [System invariants](#8-system-invariants)
9. [Module contracts and public APIs](#9-module-contracts-and-public-apis)
10. [Implementation roadmap](#10-implementation-roadmap)
11. [Risks and technical debt](#11-risks-and-technical-debt)
12. [Open questions](#12-open-questions)
13. [Decision log](#13-decision-log)
14. [Metrics evaluation](#14-metrics-evaluation)

---

## 1. Executive summary

**Prompt Compiler** is a modular, validation-first build tool that compiles structured YAML agent definitions into two production artifacts for LLM-driven medical conversational agents: a **System Prompt** (the agent's executable finite-state-machine DSL, meant to be loaded as an LLM system prompt) and a **Reference Asset** (static facts formatted for RAG retrieval, kept separate to save context-window budget).

The project grew out of the need to build and maintain voice/chat bots  without hand-writing and hand-auditing long Markdown system prompts. The current architecture (v1.0.0) is a from-scratch Pydantic v2 + Jinja2 pipeline with 11 graph validators and pluggable, YAML-declared compliance rules.

As of this writing: the compilation pipeline (`app/compiler.py`'s 7 stages), the DSL grammar (`app/schemas.py`), the renderer (`app/renderers.py` + `templates/*.j2`), and the Mermaid round-trip tooling are implemented and in active use (`dist/` shows recent local compiles). `pyproject.toml` is wired for `pytest`, but no test suite exists yet — the single largest gap between the project's stated engineering bar and its current state.

---

## 2. System objective

### 2.1 Problem

Hand-authoring a multi-hundred-line system prompt for a medical conversational agent — one that must route through qualification questions, obey Colombian data-privacy law, escalate medical emergencies, avoid paraphrasing legally-sensitive language, and never dead-end a caller in an unreachable state — is error-prone and unauditable by hand. A single missed `GO_TO` target, an inconsistent constant name, or a paraphrased legal disclaimer only surfaces once a real caller hits it in production, and there is no structural way to catch it before that happens.

### 2.2 Solution

A system that:

1. Lets authors describe an agent declaratively in YAML (identity, objectives, states, handlers, FAQs, subflows, tool contracts, compliance profile) instead of writing prose system-prompt Markdown by hand.
2. Deterministically validates the declared state graph (reachability, cycles, dangling `GO_TO` targets, duplicate ids, placeholder consistency, compliance-rule conformance) *before* any prompt is considered shippable.
3. Deterministically compiles the validated spec into a System Prompt and a Reference Asset, plus always-on diagnostic reports (validation, deduplication, orphan states) so authors can see exactly what's wrong even when a build fails.

### 2.3 Target user

**Primary user:** The engineering/conversation-design team authoring and maintaining agent YAML definitions in `agents/defs/` for non-runtime, offline authors who need fast feedback on structural and compliance errors before an agent is deployed.

**Secondary user:** Whoever operates the platform that actually loads `system_prompt.md` into a live LLM session (out of scope for this repo — this project produces the artifact, not the runtime that consumes it).

### 2.4 Strategic direction

**Decision 2026-06 (inferred from `classifier.py` §10 comment and commit history):** Grow the compiler's rigor, not its surface area — invest in validators, compliance-checker pluggability, and DSL clarity (e.g. the recent `say`/`say_verbatim` split and subflow-navigation clarifications) rather than in supporting arbitrary new output formats or runtime targets. The compiler stays a pure, offline, deterministic build tool; it does not grow into a runtime service or gain an LLM SDK dependency.

---

## 3. Scope

### Implemented phases

| Phase | Description | Domain | Status |
|---|---|---|---|
| 0 | Pydantic v2 schema layer for every YAML file type | Core | ✅ Complete |
| 1 | Loader: disk → merged, alias-resolved `AgentSpec` | Core | ✅ Complete |
| 2 | 11 graph validators + pluggable compliance-checker registry | Validation | ✅ Complete |
| 3 | Deduplicator (report-only duplicate-rule detector) | Diagnostics | ✅ Complete |
| 4 | Orphan-state report | Diagnostics | ✅ Complete |
| 5 | Classifier (System Prompt vs Reference Asset split, SSOT §10) | Core | ✅ Complete |
| 6 | Jinja2 renderer (`[[ ]]` delimiters) → System Prompt + Reference Asset (md/json) | Rendering | ✅ Complete |
| 7 | `compile_agent()` 7-stage orchestrator | Core | ✅ Complete |
| 8 | Mermaid parser (flowchart → SubflowTemplate scaffold) | Tooling | ✅ Complete |
| 9 | Mermaid diagram export (AgentSpec → PNG/HTML) | Tooling | ✅ Complete |
| 10 | `build_prompt.py` CLI + `main.py` Rich TUI | Interface | ✅ Complete |
| 11 | Channel profiles (voice/chat/async_text) + `medical_es` compliance profile | Domain content | ✅ Complete |
| 12 | Bilingual (ES/EN) system-prompt template variant | Rendering | ✅ Complete |
| 13 | FAQ model refactor (`say`/`say_verbatim` split) + retrieval-policy docs | DSL | ✅ Complete |

### Phases in progress

| Phase | Description | Domain | % Complete |
|---|---|---|---|
| 14 | Subflow-navigation and document-loading instruction clarity in `templates/system_prompt.md.j2` | Rendering/DSL | Recently landed (`d4c3557`) — verify against real agent compiles, no automated regression test yet |

---

## 4. Non-objectives

This system is NOT:

- **An LLM runtime or inference service** — it never calls a model, holds no API key, and has no HTTP server. It emits Markdown/JSON files that some other, out-of-repo platform loads.
- **A CMS for agent content** — `agents/defs/` and `agents/shared/` live in a separate, nested git repository by design; this repo does not own or version that content.
- **A general-purpose prompt-engineering framework** — the DSL, validators, and compliance model are shaped specifically around the medical voice+chat bot domain, not a generic multi-domain product.

This system does NOT:

- Deploy, host, or monitor compiled agents in production.
- Store or process real caller/patient PII at any stage (all authoring is done with synthetic example data).
- Make the System-Prompt/Reference-Asset content split configurable per agent (fixed by `classifier.py`, SSOT §10).

---

## 5. Current status

### What is working

- The full compile pipeline (`load_agent_spec` → `validate_agent_spec` → dedup → orphan report → classify → render) runs end-to-end via `build_prompt.py` and the `main.py` TUI, evidenced by recent `dist/` output.
- 11 graph validators plus the `medical_es` pluggable compliance profile catch structural and domain-compliance issues before artifacts are considered shippable.
- Subflow templating (namespacing, `@instance.export` alias resolution, `<<param>>` substitution) is implemented with hard guards (`_validate_no_unresolved_aliases`) that fail loudly rather than silently compiling a broken reference.
- Mermaid round-trip tooling (`mermaid_parser.py` scaffold-from-diagram, `mermaid_diagrams.py` export-to-diagram) is functional and documented.
- Documentation is unusually thorough for the project's size: `AGENT_CREATION_GUIDE.md` (980 lines, authoritative technical reference), `PATTERN_GUIDE.md` (compact-flow authoring pattern), `faq.md` / `handlers.md` (domain-content recommendations).

### What is incomplete

- **No automated test suite.** `pyproject.toml` declares `testpaths = ["tests"]` and `pytest>=8.0` as a dev dependency, but no `tests/` directory exists. Every validator, renderer, and loader behavior is currently verified by manual compilation of real agents only.
- **No CI.** No `.github/workflows` or equivalent — nothing currently blocks a broken commit from landing on `main`.
- The most recent template change (subflow-navigation clarification, commit `d4c3557`) has not been verified by a regression test — only by (presumably) manual inspection of a compiled prompt.

### Known technical debt

- **Legacy path translation** in `loaders._resolve_path` (`configs/{id}/...` → `agents/defs/{id}/...`) is kept for backward compatibility with pre-refactor agent directories. Revisit once it's confirmed no agent definition still relies on the old path shape.
- **`gestantes_old.md`** is a 2137-line historical/draft compiled prompt predating the `HARD_TOOL_EXECUTION_CONTRACT` section — kept at the repo root as reference material, not part of the live pipeline. Consider moving it into a `docs/archive/` or similar location so it doesn't read as live documentation.
- **`faq.md` / `handlers.md`** are informal, consulting-transcript-style Spanish documents (recommendations, not authoritative schema) sitting at repo root alongside authoritative docs like `AGENT_CREATION_GUIDE.md` — easy to mistake one for the other. No action required, but flagged as a readability risk.

### 5.1 Known bugs and DSL gaps (code audit, 2026-08-06)

A full read of every `app/` module plus both Jinja templates (see decision log entry below) found the following, ranked by severity. None have been fixed yet — this is the audit record; fixes are tracked as roadmap items in §10.

| # | Severity | Location | Finding |
|---|---|---|---|
| B1 | **High** | `templates/system_prompt.md.j2` (line 239), `templates/system_prompt_bilingual_text.md.j2` (line 244) | `[[ input_variables_block ]]` is rendered **twice** in both templates — once in its correct position after `system_constants_block`, and again as a dangling, header-less block at the very end of the file. Every compiled agent's system prompt currently duplicates its entire `# INPUT VARIABLES` section, wasting context-window tokens on every single build. |
| B2 | **High** | `app/schemas.py::FlowObjectBase.validate_semantics()` | Per-node-type invariants are incomplete/asymmetric. `execute` is forbidden only for `message`/`registration` and required for `action` — `question`, `decision`, `terminal`, `start`, `subflow_change` have no constraint on it at all, so e.g. a `question` node can silently carry an `EXECUTE` block, contradicting `TYPE_SEMANTICS` in the template (only `action` is documented as executing tools). Likewise `final='yes'` is only constrained for `terminal`/`start`/`subflow_change` — `message`/`question`/`decision`/`action`/`registration` can set `final='yes'` without `type=terminal`, and `app/utils.py::terminal_state_ids()` treats **any** state with `final=='yes'` as terminal regardless of `type` (its own docstring calls this a "legacy" dual check), so a mistyped `final: yes` on a non-terminal node silently pulls it into the terminal set used by the reachability graph. `capture`/`store` are similarly unconstrained on types where they're semantically meaningless (`terminal`, `decision`, `message`). Also asymmetric: `decision` is the only type where omitting `wait` entirely is valid (`None` passes); every other non-question type requires an explicit `wait: no` or fails validation — a `decision` node's compiled `WAIT` line is present or absent purely based on the author's spelling-it-out habit, not semantics. |
| B3 | **High** | `app/utils.py::resolve_state_alias_targets`, `resolve_slot_aliases`, `substitute_params` | All three raise a bare `ValueError` when a `@instance.export` alias or `<<param>>` can't be resolved. `app/loaders.py::load_agent_spec()` and `app/compiler.py::compile_agent()` both document — and otherwise consistently use — `RuntimeError` for every other failure mode ("aliases survive the resolution pass", missing files, bad YAML). A caller catching `RuntimeError` per the documented contract will **not** catch a broken alias or a missing template parameter. |
| B4 | **Medium-High** | `app/utils.py::GOTO_RE`; `app/validators.py::_validate_goto_targets`; `app/loaders.py::_validate_no_unresolved_aliases` | `GO_TO` detection is case-sensitive and, for the alias hard-guard, an exact-single-space substring check. A typo'd case (`go_to:` / `Go_To:`) in a `route`/`fallback` line is invisible to `extract_goto_targets` **and** to the validator's own "is this unparseable" check (same case-sensitive substring), so the line is silently treated as having no target — no error, no graph edge, just a hidden dead end. Separately, `_validate_no_unresolved_aliases` checks the literal substring `"GO_TO: @"` (one space); a malformed alias with irregular whitespace (e.g. two spaces) that also fails to match the resolution regex slips past both resolution and the guard and ships as literal broken text in the compiled prompt. |
| B5 | **Medium** | `app/renderers.py::_extract_goto_targets` (local, inside `render_subflow_document`) | Duplicates `app/utils.py::extract_goto_targets` with a narrower regex (`[A-Z][A-Z0-9_]*` only — no dynamic `[slot]` or alias support). A `subflow_change` node whose exit is a dynamic `GO_TO: [resume_state]` renders as `—` (no target) in the subflow document's exit table, even though a real dynamic exit exists. |
| B6 | **Medium** | `app/validators.py::_check_requires_disclaimer_node` | Scans `goal`+`do` text for disclaimer keywords, not the `say` content the caller actually hears. Can pass with no `SAY` block ever delivering a privacy disclaimer, and can fail despite a correct spoken disclaimer if the internal notes don't happen to use the exact keyword list. |
| B7 | **Low-Medium** | `app/loaders.py::_merge_tool_fragments` vs `_merge_tool_contract_fragments` | The same authoring mistake (declaring the same name in two shared fragment files) is handled inconsistently: tool names are silently deduplicated, tool contract names raise `RuntimeError`. |
| B8 | **Low** | `app/validators.py` (missing validator) | No check for phrase collisions between global `HANDLER.trigger` and `FAQ.match` (only FAQ-vs-FAQ collisions are checked). Since `GLOBAL_HANDLERS` are evaluated before `FAQ_POLICY` per the template's `EXECUTION_ORDER`, an overlapping phrase can make an FAQ permanently unreachable with no diagnostic. |
| B9 | **Low** | `app/schemas.py` (missing validator) | No warning when a `question`/`registration` node declares no `capture`/`store` at all — schema allows a "question" that captures nothing, likely an authoring mistake given `PATTERN_GUIDE.md`'s documented question→decision pattern. |
| B10 | **Low** | `app/validators.py::_validate_question_self_loops` | Retry-counter detection is a fixed substring match on `"_retry_count"` across `do+store+route+fallback` text, not a check for an actual declared/incremented/compared slot. An author using a different name gets a false "safe" pass with no real retry protection. |
| B11 | **Medium** | `app/validators.py::_validate_question_self_loops` | Found by reading a real production-style agent while updating `PATTERN_GUIDE.md` (2026-08-06). The check only fires when a `question` node's own `route`/`fallback` targets itself. But the compact, PATTERN_GUIDE.md-recommended pattern (`question` → `decision` → back to the *question*) has the **decision** node routing back to the question, not the question routing to itself — so this validator structurally never fires for the exact pattern the project's own authoring guide recommends. It provides no protection against a genuinely missing retry counter in the common case; only the rare direct question-to-self loop is covered. |

---

## 6. Target architecture

### Architecture diagram

```
                      ┌─────────────────────────────┐
                      │   agents/defs/<agent_id>/    │   (separate nested git repo,
                      │   agents/shared/             │    gitignored by this repo)
                      └──────────────┬───────────────┘
                                     │  YAML on disk
                                     ▼
                        ┌────────────────────────┐
                        │   app/loaders.py        │  disk → merged AgentSpec
                        │   (+ app/schemas.py,    │  (subflow instantiation,
                        │      app/utils.py)      │   alias resolution, namespacing)
                        └───────────┬─────────────┘
                                    ▼
                     ┌──────────────────────────────┐
                     │  app/classifier.py (SSOT §10) │  split: prompt-bound vs
                     └──────────────┬───────────────┘  reference-bound content
                                    ▼
        ┌───────────────────────────────────────────────────┐
        │  app/validators.py (11 graph validators +           │
        │  pluggable compliance checkers, profiles/compliance)│
        │  app/deduplicator.py (report-only)                  │
        └──────────────────────┬────────────────────────────┘
                                ▼
                    ┌────────────────────────┐
                    │   app/renderers.py       │  Jinja2, custom [[ ]] delimiters
                    │   templates/*.j2         │
                    └───────────┬─────────────┘
                                ▼
        ┌──────────────────────────────────────────────────┐
        │  dist/<agent_id>/                                  │
        │    system_prompt.md   reference_asset.{md,json}    │
        │    reports/{validation,deduplication,orphan}.md    │
        └──────────────────────────────────────────────────┘

Orchestrated by app/compiler.py::compile_agent() (7 stages: Load → Classify →
Validate → Deduplicate → Detect orphans → Render → [caller writes to disk]).
Entry points: app/build_prompt.py (CLI), main.py (Rich TUI),
app/scaffold_from_mermaid.py (Mermaid → YAML scaffold CLI).
```

### Architectural principles

1. **The compiler is pure.** `compile_agent()` takes an agent directory and compilation params and returns rendered artifacts + reports in memory. It never touches the filesystem beyond reading source YAML. File writing is the caller's (`build_prompt.py`, `main.py`) responsibility.
2. **Diagnostics are unconditional; artifacts are gated.** Validation, deduplication, and orphan-state reports are always produced. `system_prompt.md` / `reference_asset.*` are only written when validation passes (or the caller explicitly overrides).
3. **The DSL grammar is centralized in `schemas.py`.** Every structural rule about what a `question`/`decision`/`action`/`terminal` node may or must contain lives in one Pydantic `model_validator`, not scattered across renderers or validators.
4. **Compliance is data, not code.** Rule severity and applicability are declared in `profiles/compliance/*.yaml`; Python only supplies the checker functions the registry dispatches to.

### Component hierarchy

Dependencies flow strictly downward. A module at a given level may depend on modules at the same or lower level only through the interfaces below — never introduce a dependency that runs the other direction (e.g. `schemas.py` importing from `renderers.py`).

```
Level 5 — Entry points        build_prompt.py, main.py, scaffold_from_mermaid.py
Level 4 — Orchestration       compiler.py
Level 3 — Rendering           renderers.py  (depends on classifier output + utils)
Level 2 — Analysis            validators.py, classifier.py, deduplicator.py,
                               mermaid_parser.py, mermaid_diagrams.py
                               (all operate on an already-built AgentSpec)
Level 1 — Assembly            loaders.py  (schemas + utils → AgentSpec)
Level 0 — Foundation          schemas.py, utils.py  (no internal app/ dependencies
                               beyond each other)
```

**Rule:** `schemas.py` never imports from any other `app/` module. `utils.py` never imports from anything above Level 0. `renderers.py` never bypasses `classifier.py` to decide what belongs in which artifact.

---

## 7. Architectural decisions

### Decision 2026 (v2.0.0 rewrite) — Pydantic v2 schema-first DSL over freeform Markdown authoring

**Decision:** Represent every agent construct (states, handlers, FAQs, subflows, tool contracts) as a Pydantic v2 model with `extra="forbid"` and validated regex-constrained identifiers, loaded from YAML.

**Discarded alternative:** Continue hand-authoring system prompts directly as Markdown (the `gestantes_old.md` approach).

**Reason:** Hand-authored prompts have no structural validation — a dangling `GO_TO`, an inconsistent constant name, or a missing retry counter is invisible until a live call hits it. A typed schema catches these at build time.

**Consequences:** Every new DSL feature must be expressed as a schema change first (`schemas.py`), then plumbed through `loaders.py`, `validators.py`, `renderers.py`, and `templates/*.j2` in lockstep — a heavier process than editing Markdown directly, traded for build-time safety.

### Decision (undated, inferred from `classifier.py` docstring) — Fixed prompt/reference-asset split (SSOT §10)

**Decision:** The rule that decides whether a given field belongs in the System Prompt or the Reference Asset is fixed logic in `classifier.py`, not a per-agent or per-call configuration option.

**Discarded alternative:** Expose classification as a `CompilationParams` field so individual agents could tune what goes where.

**Reason:** Making it configurable would let two agents built from structurally identical source data diverge in what an LLM actually sees at runtime vs. what only lives in retrieval — undermining the guarantee that "the same kind of content always ends up in the same kind of artifact."

**Consequences:** Any request to make classification "flexible per agent" needs a human decision here (§12), not a silent code change.

### Decision (nested repo layout) — `agents/` versioned separately from the compiler

**Decision:** `agents/defs/` and `agents/shared/` live in their own nested git repository, excluded from this repo's version control.

**Discarded alternative:** Version agent content in the same repo as the compiler.

**Reason:** Compiler releases and agent-content changes have different review cadences, different owners (engineering vs. conversation design), and different sensitivity (agent content may reference real clinic pricing/policy that shouldn't ship with every compiler tag).

**Consequences:** Tooling and CI in this repo must never assume `agents/` content is present, tracked, or safe to commit through the top-level `git`. Tests must use synthetic fixture YAML, not real `agents/defs/` content.

---

## 8. System invariants

The following invariants apply to all changes, without exception. They are the rules `@guardian` and `@dsl-expert` always verify (see EXPERTS.md):

1. **`compile_agent()` performs no file I/O.** File writing is the caller's responsibility.
2. **Rendering is unconditional; artifact-writing is conditional on validation.** Diagnostic reports are always produced; `system_prompt.md`/`reference_asset.*` are gated on a clean (or explicitly overridden) validation pass.
3. **The classifier's prompt/reference-asset split is fixed, not configurable per agent** (SSOT §10).
4. **Naming regexes are enforced, not advisory:** `lower_snake_case` for agent/slot/tool ids, `UPPER_SNAKE_CASE` for constants/state/handler/FAQ ids, `^[A-Z]+_[0-9]+$` for compliance `rule_id`.
5. **Subflow namespacing always uses a double underscore** (`NAMESPACE__STATE_ID`, `namespace__slot`).
6. **No unresolved `@instance.export` alias may reach a rendered artifact** — `_validate_no_unresolved_aliases` must raise, not warn, on any survivor.
7. **`say_verbatim` content is never silently defaulted or flipped** — it is a compliance signal, and changing it changes what a live medical/legal disclosure looks like to a caller.
8. **Compliance rule severity lives in `profiles/compliance/*.yaml`,** never hardcoded in `validators.py`.
9. **Compilation is deterministic** — identical source YAML produces byte-identical output, every time, regardless of dict ordering, timestamps, or environment.
10. **No real PII (names, phone numbers, cédula numbers, medical history) ever enters the repo**, in code, fixtures, tests, or docs.

---

## 9. Module contracts and public APIs

### `app/compiler.py::compile_agent()`

**Single responsibility:** Orchestrate the 7-stage pipeline (Load → Classify → Validate → Deduplicate → Detect orphans → Render) for one agent and return everything the caller needs to decide what to write to disk.

**Inputs:**
- `agent_dir: Path` — directory containing the agent's `manifest.yaml` and includes.
- `params: CompilationParams` — frozen dataclass: channel, verbosity, reference-asset formats, compliance profile, `embed_subflows`.

**Outputs:**
- Rendered System Prompt and Reference Asset content (in memory), plus validation/dedup/orphan diagnostic reports.

**Guarantees:**
- No filesystem writes.
- Renders even when validation has errors.

**Never does:**
- Write files.
- Deploy or transmit output anywhere.

### `app/classifier.py`

**Single responsibility:** Deterministically split an `AgentSpec`'s content into System-Prompt-bound vs. Reference-Asset-bound fields (SSOT §10).

**Inputs:** `AgentSpec`.

**Outputs:** Classified content ready for `renderers.py`.

**Guarantees:** Stateless; identical input always produces identical classification.

**Never does:** Accept a configuration parameter that changes the classification rule per call.

### `app/validators.py::validate_agent_spec()`

**Single responsibility:** Run all structural and compliance checks against an `AgentSpec` and return a validation report (errors + warnings), never raising for a normal invalid spec (raising is reserved for programmer-error conditions like unresolved aliases, which are caught earlier in `loaders.py`).

**Inputs:** `AgentSpec`, active compliance profile.

**Outputs:** Validation report (list of errors/warnings with location context).

**Guarantees:** Runs every registered check; a check that finds nothing reports nothing (no false positives by default).

**Never does:** Mutate the `AgentSpec` it validates.

---

## 10. Implementation roadmap

### Upcoming phases

| Phase | Description | Priority | Completion criterion |
|---|---|---|---|
| 15 | Stand up `tests/` with unit coverage for `schemas.py` validators, `loaders.py` alias resolution, and `app/utils.py` regex helpers | High | `pytest tests/unit -q` passes; at least one test per public schema validator and per regex helper |
| 16 | Integration test compiling a minimal synthetic agent end-to-end (`tests/integration/test_compile_agent.py`) | High | Compiling a fixture agent produces expected `system_prompt.md` content byte-for-byte |
| 17 | CI workflow (`.github/workflows/ci.yml`) running ruff + pytest on every push/PR | Medium | CI badge green on `main`; a failing test blocks merge |
| 18 | Regression test for the `say`/`say_verbatim` FAQ split and subflow-navigation template change (`d4c3557`) | Medium | A rendered fixture prompt is asserted to contain the expected `HARD_TOOL_EXECUTION_CONTRACT`/subflow-navigation instructions |
| 19 | **Fix B1** — remove the duplicated trailing `[[ input_variables_block ]]` in both `templates/*.j2` | High | A rendered fixture prompt contains exactly one `# INPUT VARIABLES` section; regression test added first (RED) before the template edit |
| 20 | **Fix B2** — complete `FlowObjectBase.validate_semantics()`'s per-type invariants (`execute`, `final`, `capture`/`store` restricted to the types where they're meaningful; normalize `wait` handling for `decision`) | High | New schema tests (one per now-forbidden type/field combination) fail on current code, pass after the fix; requires a human decision first on the exact intended matrix (see open question 3) |
| 21 | **Fix B3** — wrap `resolve_state_alias_targets`/`resolve_slot_aliases`/`substitute_params` failures into `RuntimeError` (or update the documented contract to mention `ValueError`) | Medium | `load_agent_spec()` raises the documented exception type for a broken alias fixture; test added first |
| 22 | **Fix B4** — make `GO_TO` detection and the unresolved-alias guard whitespace/case robust (or explicitly document `GO_TO:` as case-sensitive-by-design and add an `UNPARSEABLE_GOTO`-style catch for near-miss casing) | Medium | A fixture with `go_to:`/irregular alias spacing produces an explicit validation error instead of silently compiling |
| 23 | **Fix B5** — `render_subflow_document` reuses `app.utils.extract_goto_targets` instead of its local narrower regex | Low | A fixture `subflow_change` node with a dynamic `GO_TO: [slot]` exit shows the real target in the subflow document, not `—` |
| 24 | Add a `HANDLER.trigger` vs `FAQ.match` phrase-collision validator (addresses B8) | Low | A fixture with an identical trigger/match phrase produces a warning |

### Findings not yet scheduled (need a product decision first)

B6 (disclaimer checker should scan `say`, not `goal`/`do`), B7 (tool vs. tool-contract duplicate-handling asymmetry), B9 (missing capture/store warning for question/registration), B10 (retry-counter heuristic is a text-substring match), and B11 (the self-loop check never fires for the recommended question→decision→question pattern, only for a direct question-to-self loop) are logged in §5.1 but intentionally left unscheduled — each implies a small behavior change to what currently validates cleanly, which per CLAUDE.md §5.1.6 ("propose and wait") needs an explicit go-ahead rather than being bundled into the test-suite work. B10 and B11 together mean `_validate_question_self_loops` currently provides close to no real protection in practice — worth prioritizing once a decision is made.

### Discarded phases (and why)

| Phase | Description | Reason for discarding |
|---|---|---|
| — | Runtime hosting / API server for compiled agents | Out of scope by design (§4) — this repo produces artifacts, it does not serve them |
| — | Configurable per-agent prompt/reference-asset classification | Rejected — undermines SSOT §10 guarantee (§7 decision log) |

---

## 11. Risks and technical debt

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| No automated tests — a change to `schemas.py`/`renderers.py`/`validators.py` silently breaks a compiled agent | High (given current state) | High — could ship a broken or non-compliant prompt to a live medical bot | Stand up `tests/` (roadmap phase 15–16) before further DSL changes |
| **[B1, confirmed]** Every compiled agent's system prompt currently ships a duplicated `# INPUT VARIABLES` section (double render of `input_variables_block`) | Certain — reproducible on every build | Medium — wastes prompt tokens on every live agent, no functional breakage observed | Fix scheduled as roadmap phase 19 |
| **[B2, confirmed]** Incomplete per-node-type schema invariants let semantically invalid field combinations (e.g. `EXECUTE` on a `question` node) pass validation silently | Medium — depends on authoring mistakes | Medium-High — could compile a prompt where the LLM is told to run a tool call from a state the template's own `TYPE_SEMANTICS` says shouldn't have one | Fix scheduled as roadmap phase 20, pending a human decision on the exact target invariant matrix (open question 3) |
| **[B3, confirmed]** Broken alias/param references raise `ValueError` instead of the documented `RuntimeError` | Low — only triggers on already-broken input | Low-Medium — mostly a caller-ergonomics issue (wrong except clause), not a data-correctness issue | Fix scheduled as roadmap phase 21 |
| `gestantes_old.md` mistaken for current spec by a new contributor | Medium | Low-medium | Move to an `archive/` subfolder or add a header banner marking it historical |
| Legacy `configs/{id}/...` path support in `loaders._resolve_path` masks a misconfigured agent directory | Low | Low | Revisit once confirmed no live agent depends on the old path |
| Compliance profile YAML edited to silently downgrade an error to a warning | Low (requires human edit) | High if it happens — could let non-compliant content ship | §5.2.8/§6 of CLAUDE.md: compliance severity changes always require human sign-off |

---

## 12. Open questions

| # | Question | Who decides | Open since | Blocks |
|---|---|---|---|---|
| 1 | Should `gestantes_old.md`, `faq.md`, `handlers.md` move into a `docs/` subdirectory to separate authoritative specs from historical/consulting material? | Repo owner | 2026-08-06 | Repo root cleanliness; not a functional blocker |
| 2 | What is the target test-coverage bar before the next DSL grammar change lands (roadmap phase 15)? | Repo owner | 2026-08-06 | Whether new DSL features can proceed without tests in the interim |
| 3 | For B2 (roadmap phase 20): what is the intended full per-type invariant matrix for `execute`/`final`/`capture`/`store`/`wait`? (E.g., should `question` nodes be required to `capture`? Should `decision` require an explicit `wait: no`?) | Repo owner / `@dsl-expert` | 2026-08-06 | Implementing the schema fix for B2 |
| 4 | Should `GO_TO:` matching stay strictly case-sensitive by design (simplicity) or become case-insensitive/whitespace-tolerant (robustness)? (B4) | Repo owner / `@dsl-expert` | 2026-08-06 | Implementing the fix for B4 |

---

## 13. Decision log

### 2026-08-06 — Code audit of the DSL/compiler pipeline; findings logged, test suite started

**Context:** User requested an evaluation of the codebase to identify problems and gaps in the DSL state machine (to inform the roadmap) and the creation of the test suite the project has been missing since v2.0.0.

**Decision:** Read every `app/` module (`schemas.py`, `utils.py`, `loaders.py`, `validators.py`, `classifier.py`, `deduplicator.py`, `compiler.py`, `renderers.py`) and both Jinja templates in full. Logged 10 concrete findings (B1–B10) in §5.1, ranked by severity. Scheduled fixes for the clearer/lower-risk ones (B1, B3, B4, B5, B8) as roadmap phases 19/21/22/23/24; deferred the ones implying a behavior/spec decision (B2, B6, B7, B9, B10) pending explicit sign-off per CLAUDE.md §5.1.6. Then built out `tests/unit/app/` and `tests/integration/` per TESTING.md's existing spec, covering the modules audited.

**Alternatives considered:**
- Fixing bugs inline during the audit — rejected; CLAUDE.md §5.1.6 ("propose and wait") and the explicit user ask ("evaluar... para identificar el roadmap") frame this session as audit + test-infrastructure, not a bug-fix session. Findings are logged for a follow-up, human-confirmed pass.
- Writing tests that assert the *current* (buggy) behavior for B1–B10 as if it were correct — rejected per DESIGN_PATTERNS.md anti-pattern A10 (retrospective test). Instead, known-bug findings are either left untested at the unit level (documented in SPEC.md only) or covered by a small number of `xfail(strict=True)` regression tests that state the *intended* behavior, so a future fix flips them to passing and the marker must be removed.

**Consequences:** SPEC.md §5.1/§10/§11/§12 are now the authoritative bug/roadmap tracker for this pipeline. The next DSL change should check this list before assuming a clean slate. Test suite coverage is no longer zero (see STATUS.yaml for the exact count) but is not yet exhaustive — phases 15/16/17 in §10 remain open for full coverage and CI.

### 2026-08-06 — Adopted the framework_example process templates for this repo

**Context:** `framework_example/` contained a generic 7-document development-process framework (CLAUDE.md, SPEC.md, EXPERTS.md, FRAMEWORK.md, TESTING.md, STATUS.yaml, DESIGN_PATTERNS.md). The user asked for it to be adapted into project-specific root-level documents for Prompt Compiler.

**Decision:** Create adapted versions of all 7 documents at the repo root, filled in with facts derived from `app/`, `templates/`, existing docs (`AGENT_CREATION_GUIDE.md`, `PATTERN_GUIDE.md`, `faq.md`, `handlers.md`), `pyproject.toml`, and git history — rather than leaving the generic templates in place.

**Alternatives considered:**
- Leave `framework_example/` as-is and reference it — rejected, since the whole point of the framework is that it's read every session; an unfilled template with `{{PLACEHOLDER}}` tokens doesn't function as a real policy document.

**Consequences:** These 7 files now govern how future sessions on this repo declare, implement, and review work. They should be kept up to date per FRAMEWORK.md §8 (session close checklist) as the project evolves — especially once the test suite (open question, roadmap phase 15) actually exists.

---

## 14. Metrics evaluation

| Metric | Target | Current status | Last reviewed |
|---|---|---|---|
| Test coverage | Non-zero, growing toward core-module coverage | 0% — no `tests/` directory exists | 2026-08-06 |
| CI presence | Green pipeline on every push to `main` | None configured | 2026-08-06 |
| Validators passing on all live agent defs | 100% (no error-level findings) | Unknown — not machine-checked outside manual compiles | 2026-08-06 |
| Compliance profile coverage (`medical_es`) | Applies to all medical-domain agents | Applied per `AGENT_CREATION_GUIDE.md`; not independently verified here | 2026-08-06 |

---

*Update §5 (Current status) and §13 (Decision log) at each work session. Update §12 (Open questions) when a new question is opened or an existing one is closed.*
