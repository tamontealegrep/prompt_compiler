# FRAMEWORK.md — Development Process: Prompt Compiler

> Version: 2.0.0 | Date: 2026-08-06
> Connects CLAUDE.md (policies), SPEC.md (specification), and EXPERTS.md (panel) with the real work of each session. Read after CLAUDE.md and before starting any task.

---

## 0. Why this document exists

CLAUDE.md defines the rules. SPEC.md defines what gets built. EXPERTS.md defines who reviews what. This document defines the **process** that connects the three with the real work: how a task is transformed into production code, going through design, implementation, review, and verification.

The central distinction that shapes everything: **output vs. outcome**.

---

## 1. Output vs. Outcome

### 1.1 The distinction applied to Prompt Compiler

| Requested as output | Requested as outcome |
|---|---|
| "Add a new FAQ node type to the DSL" | "That an agent author can declare a FAQ that answers a question without derailing the current state, and the compiler refuses to build if the FAQ's resume target doesn't exist" |
| "Fix the subflow namespacing bug" | "That two agents instantiating the same SubflowTemplate never collide on state or slot ids, and the compiler catches it at build time if they would" |
| "Make the compliance profile stricter" | "That content requiring official legal/medical review is never silently compiled into a shippable prompt without a human having explicitly marked it reviewed" |
| "Write tests for the loader" | "That a future change to `loaders.py`'s alias-resolution logic cannot silently ship a broken `GO_TO`/`@instance.export` reference — the test suite catches it before a human does" |

### 1.2 Why constraints are the real work

Asking for a well-formed outcome requires **more** specification than asking for an output, but of a different kind:

- The procedure is not specified step by step.
- **What is specified** are the limits the result cannot cross.
- **What is specified** is how to measure whether the outcome was achieved (verifiable criterion).
- **What is specified** is what happens in the cases that are not the average case.

### 1.3 The average-case trap

An LLM agent, faced with any ambiguous instruction, fills in the gaps with what is statistically most likely — which is almost always the "standard" case. This is not a prompting defect; it is a structural property.

In this project, the "cases that are not the average case" are concrete: the agent with zero states in a subflow, the FAQ whose `faq_resume_to` target was renamed, the compliance rule whose severity was silently downgraded, the slot alias pointing at an export the target subflow never declares. These are exactly where an unvalidated change fails — and because the output is a system prompt fed to another LLM at runtime, the failure surfaces to a real caller, not to a stack trace.

The structural solution is to move constraints out of the prompt and into the system files (CLAUDE.md, SPEC.md, EXPERTS.md) that are always loaded, not only when someone remembers them.

---

## 2. The three pillars

### Pillar 1 — Pipeline: no stage decides what belongs to the next

**Principle:** Work is divided into stages with explicit gates. No advancement without the previous stage having a verified verifiable criterion.

**Standard development pipeline (integrated TDD):**

```
[1] Task defined as outcome
       ↓  (is it in SPEC.md? which experts apply?)
[2] Scope declared
       ↓  (files to touch / files NOT to touch / verifiable criteria)
[RED]  Tests written — must FAIL
       ↓  (one test per success criterion declared in [2]; if it passes without code, it is wrong)
[GREEN] Minimum implementation that makes the tests pass
       ↓  (minimum code — no abstractions, no "while I'm at it")
[REFACTOR] Cleanup without breaking the tests
       ↓  (CLAUDE.md §2 and §3; run tests after every change)
[3] Panel review
       ↓  (each expert reviews their own metrics, not the other's — see EXPERTS.md)
[4] Verification
       ↓  (full suite passes: unit + integration; ruff check .)
[5] Complete
       ↓  (STATUS.yaml updated / decision in SPEC.md §13 if applicable)
```

**The gate rule:** a stage only advances when its completeness criterion is met and has been verified. The RED step is mandatory: if there is no failing test before implementation, there is no TDD.

**Current state note:** the project has no `tests/` directory yet (SPEC.md §5, §10 phase 15-16). Until it exists, the RED step means: write the test file that should exist, run it, confirm it fails for the right reason (missing behavior, not a typo), then implement. Do not skip RED because "there's no test infrastructure yet" — that is precisely the gap being closed.

See TESTING.md for the complete Red-Green-Refactor cycle, test organization, and the types of tests required per component type.

**Compilation pipeline (the product's own pipeline — distinct from the development pipeline above):**

```
[Agent author edits YAML in agents/defs/<agent_id>/]
      ↓
[Load]  loaders.py: disk → merged, alias-resolved AgentSpec
      ↓
[Classify]  classifier.py: split prompt-bound vs. reference-bound content (SSOT §10)
      ↓
[Validate]  validators.py: 11 graph validators + compliance profile checks
      ↓
[Deduplicate]  deduplicator.py: report-only duplicate-rule detection
      ↓
[Detect orphans]  orphan-state report
      ↓
[Render]  renderers.py + templates/*.j2 → System Prompt + Reference Asset
      ↓
[Caller writes to disk]  build_prompt.py / main.py → dist/<agent_id>/
      ↓
[Human review]  before a compiled prompt is handed to whatever platform loads it into a live LLM session
```

**Rule:** every action that puts compiled content in front of a real caller has a human at the end — this repo never auto-deploys.

### Pillar 2 — Memory: work is resumable and auditable

**Principle:** A session without a written trace starts from zero the next time — it forgets decisions already made, repeats already-corrected mistakes, and cannot show anyone what was decided and why.

**The memory artifacts of Prompt Compiler:**

| Artifact | Where it lives | What it records | Updated |
|---|---|---|---|
| Architectural decisions | `SPEC.md §7` and `§13` | What was decided, why, what was discarded | When a non-trivial decision is made |
| Invariants | `SPEC.md §8` | Rules that no change can break | When a new invariant is discovered |
| Project state | `STATUS.yaml` | Where we are, what is open | Every session |
| Review criteria | `EXPERTS.md` | What each expert verifies | When a new error pattern appears |
| Proven patterns | `DESIGN_PATTERNS.md` | What works, what doesn't | When a pattern is confirmed |
| Agent-authoring conventions | `AGENT_CREATION_GUIDE.md`, `PATTERN_GUIDE.md` | How to write compact, correct agent YAML | When the DSL grammar or a proven authoring pattern changes |

**Maintenance rule:** when an error pattern appears in more than one change, it is not only corrected in the PR where it appeared — it becomes a rule in CLAUDE.md or a criterion in EXPERTS.md.

### Pillar 3 — Panel: disagreement is not averaged, it is preserved

**Principle:** Asking a single agent to evaluate "from all angles" produces soft consensus. For real disagreement to emerge, each perspective needs its own isolated criteria.

**How the panel works:**

1. The EXPERTS.md "When each expert is activated" table determines which experts apply to the change.
2. Each expert reviews with their specific metrics — not the other's.
3. If two experts have contradictory criteria (e.g. `@dsl-expert` wants a field optional for authoring convenience, `@compliance-expert` wants it mandatory to force a verbatim-content decision), the conflict is documented in `SPEC.md §13` before implementing — it is not resolved alone.
4. The decision-maker is a person (the repo owner), not the average of the perspectives.

**The counterintuitive part:** the goal of the panel is not to reach consensus. It is to make disagreement visible and informed.

---

## 3. The task declaration template

Before writing code, fill in this template:

```
## Task: [title]
Date: YYYY-MM-DD
SPEC.md Phase: [phase number from §3/§10, or "not on roadmap — justify"]

### Expected outcome
[Reformulate the task as an outcome, not as an output. What changes for an agent author
or for a compiled agent's behavior once done?]

### Scope
Files to touch:
  - [file 1 — what specific change]
  - [file 2 — what specific change]

Files NOT to touch:
  - [file that could be confused as a candidate — why not]

### Tests to write (BEFORE the code)
  - [ ] tests/unit/[module]/test_[component].py — [what behavior it verifies]
  - [ ] tests/integration/[domain].py — [if applicable: what end-to-end compile flow it verifies]

### Success criteria (what the tests must verify)
  - [ ] [criterion 1 — the test that verifies it and the expected result]
  - [ ] [criterion 2 — the test and the expected result]

### Required experts
@guardian + [list of experts per EXPERTS.md's activation table]
```

**Rule:** if verifiable criteria cannot be defined before starting, the task needs more clarification — not more code. Tests are written as the first step of implementation, not the last.

---

## 4. Task lifecycle

```
not_started → declared → in_implementation → in_review → verified → complete
                                                  ↓ (if unresolvable blocker)
                                               blocked
                                                  ↓ (blocker resolved in follow-up session)
                                              in_review
```

| State | Entry condition | Exit condition |
|---|---|---|
| `declared` | Declaration template complete, experts identified | — |
| `in_implementation` | — | Code written, tests written **and executed** — passing output observed |
| `in_review` | — | Each expert executed their metrics |
| `verified` | — | All success criteria marked as ✓ |
| `complete` | — | STATUS.yaml updated; decision in SPEC.md §13 if applicable |
| `blocked` | Panel returned BLOCKED and the blocker requires a design decision that cannot be made in this session | Blocker resolved in a follow-up session → back to `in_review` |

**No silent rollbacks.** If a task returns to a previous state, the reason is recorded in STATUS.yaml.

**When a task enters `blocked`:**
1. Record in STATUS.yaml: which expert blocked it, the exact blocker text, and what decision is needed to unblock.
2. If the implementation is partial, revert to the last consistent state before closing the session.
3. The follow-up session starts by reading the blocked task and resolving the open decision — record the resolution in SPEC.md §13 — before resuming `in_review`.

---

## 5. Process hard rules

Regardless of the change type, these process rules are always active:

1. **Declare before implementing.** No change starts without the declaration template filled in.
2. **The diff traces to the declared scope.** Every file touched appears in the scope list.
3. **Criteria are executed, not declared.** "Tests pass" means they were run, not that they would presumably pass.
4. **Disagreement between experts is documented, not resolved by inference.** Goes to SPEC.md §13.
5. **New knowledge goes into the system.** A decision made in a session but not written into any file is knowledge that is lost.
6. **STATUS.yaml is updated at the end of every session.** The next agent (or next session) starts by reading the real state, not reconstructing from scratch.
7. **Hard rule overrides follow the emergency procedure.** Any rule from CLAUDE.md §5 broken without following §5.3 (state explicitly → human authorization → log in STATUS.yaml) is a silent violation, not an emergency exception.
8. **Run tests after every code change.** After modifying any source file in `app/`, run the affected test modules immediately — don't wait until the task is declared `in_review`. Use `.venv/Scripts/python.exe -m pytest ...` (Windows), not the system Python.
9. **A DSL grammar change is never "just" a `schemas.py` edit.** Per EXPERTS.md `@dsl-expert`: it also touches `renderers.py`/`templates/*.j2` and `AGENT_CREATION_GUIDE.md`/`PATTERN_GUIDE.md` in the same task, or it is incomplete.

---

## 6. Orchestration table: when the AI acts and when a human acts

| Decision type | Who decides? | Automation or orchestration? |
|---|---|---|
| Adding a test for existing, already-shipped behavior | AI alone | Automation — closes a known coverage gap, no judgment call |
| Fixing a validator/renderer bug with a clear, reproducible symptom | AI proposes scope, human confirms | Orchestration — the correct fix depends on which of the four coupled DSL surfaces is actually wrong |
| Adding a new DSL construct, node type, or compliance rule | AI drafts, human confirms before implementing | Orchestration — changes what every future compiled agent can express |
| Editing `agents/defs/`/`agents/shared/` content (separate repo, feeds a live medical bot) | Human always, AI as support | Non-delegable — affects what a real caller hears |
| Changing compliance profile severity or the SSOT §10 classifier split | Human always | Non-delegable — legal/compliance and architectural-guarantee risk |

---

## 7. How a development session starts

Checklist at the start of any session:

```
[ ] Read CLAUDE.md — rules that apply always
[ ] Read SPEC.md §8 — invariants that cannot be broken
[ ] Read STATUS.yaml — where the work left off, what is open
[ ] In STATUS.yaml: read `next_session` — specific instructions left by the previous session
[ ] In STATUS.yaml: migrate any `decisions_pending_registration` entries to SPEC.md §13 before starting new work
[ ] Read the open questions in SPEC.md §12 — what is blocking future decisions
[ ] Identify the day's task as an outcome (not as an output)
[ ] Fill in the task declaration template (FRAMEWORK.md §3)
[ ] Identify the relevant experts via the EXPERTS.md activation table
```

---

## 8. How a development session closes

Checklist at the end of any session:

```
[ ] STATUS.yaml updated (changelog + task state)
[ ] If a non-trivial architectural decision was made → SPEC.md §7/§13 updated
[ ] If an error pattern appeared that could repeat → CLAUDE.md or EXPERTS.md updated
[ ] If an unanswered question arose → SPEC.md §12 updated
[ ] If a phase was completed → SPEC.md §3/§10 updated
[ ] The code state is consistent with the state in STATUS.yaml
[ ] If a DSL grammar change landed → AGENT_CREATION_GUIDE.md / PATTERN_GUIDE.md updated to match
```

---

*This document is the process. The other documents are the resources the process uses. If the process does not reflect how work is actually done, the process is updated — work is not done differently without documenting it.*
