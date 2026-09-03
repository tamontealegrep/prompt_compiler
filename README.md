# Prompt Compiler

A modular, validation-first system for building and maintaining conversational agents. The compiler transforms structured YAML configurations into two production-ready artifacts: a **System Prompt** (the agent's executable state machine logic) and a **Reference Asset** (static facts optimized for RAG retrieval).

---

## Features

- **Deterministic compilation** — identical source always produces identical output
- **11 graph validators** — catches dangling transitions, cycles, unreachable states, type semantic violations, and more before a prompt ever reaches a model
- **Pydantic v2 schemas** — every YAML field is type-checked and validated at load time
- **Subflow templating** — parametric, reusable flows with fully namespaced exports
- **Channel-driven policies** — voice, chat, and async\_text each enforce different policy section requirements
- **Pluggable compliance profiles** — declarative rule registry with per-rule severity (e.g., `medical_es`)
- **Reference Asset output** — RAG-friendly markdown + JSON from a single source
- **Mermaid scaffolding** — generate YAML stubs from an existing flowchart diagram
- **Interactive TUI** — terminal menu to compile, diagram, and inspect agents without memorizing CLI flags
- **Always-written diagnostic reports** — validation, deduplication, and orphan state reports written on every run, even failures

---

## Requirements

- Python 3.11+
- Dependencies: `pydantic`, `pyyaml`, `jinja2`, `networkx`, `rich`

```bash
pip install -r requirements.txt
```

---

## Quick Start

### 1. Create your agent config directory

```bash
mkdir -p agents/defs/my_agent
```

### 2. Add the required YAML files

At minimum you need `manifest.yaml` and the core files it references. See [Agent Creation Guide](AGENT_CREATION_GUIDE.md) for a full walkthrough.

### 3. Compile

```bash
python app/build_prompt.py agents/defs/my_agent --channel voice --verbosity standard
```

### 4. Inspect output

```
dist/my_agent/
├── full/                    # the monolithic prompt
│   ├── system_prompt.md
│   ├── system_prompt_mini.md   # compact-notation companion — only written when the
│   │                           # agent's template has a *_mini.md.j2 companion on disk
│   ├── reference_asset.md
│   └── reference_asset.json
├── split/                   # deploy-platform package (see below)
│   ├── system_prompt.md        # # PERSONALITY / # GOAL / # INSTRUCTIONS
│   ├── system_prompt_mini.md
│   ├── knowledge_base.md       # the CONVERSATION_FLOW block, indexed separately
│   ├── knowledge_base_mini.md
│   ├── reference_asset.md
│   └── reference_asset.json
├── reports/
│   ├── validation_report.md
│   ├── deduplication_report.md
│   └── orphan_states_report.md
└── diagrams/                 # Mermaid state diagrams (generated from the TUI)
```

`split/` is for hosting platforms that cap the agent profile field and index the flow as a knowledge base: `split/system_prompt.md` re-labels `IDENTITY`→`PERSONALITY` and `OBJECTIVES`→`GOAL`, groups the rest under `INSTRUCTIONS`, and moves the whole `CONVERSATION_FLOW` (DSL rules, handlers, FAQs, states) into `knowledge_base.md`. It is a pure re-slice — every byte comes from `full/system_prompt.md`. Subflow states are always embedded in `system_prompt.md` / `knowledge_base.md`, never written as separate files. The build summary prints each section's word count and flags any over 2000.

`system_prompt_mini.md` encodes the exact same STATES/HANDLERS/FAQS content as `system_prompt.md`, just denser — no information is dropped, only fields that are already fully implied by context (e.g. `WAIT`/`FINAL`, which are now fully determined by each node's type) are left unstated. See `SPEC.md`'s decision log for the compression rules and measured results. It has **not** been empirically validated against a live LLM yet — treat it as a second, opt-in artifact for now, not a drop-in replacement for `system_prompt.md`.

---

## Project Structure

```
.
├── app/                         # Core compilation pipeline
│   ├── compiler.py              # Orchestrator — runs the 7-stage pipeline
│   ├── loaders.py               # Reads YAML from disk and assembles AgentSpec
│   ├── schemas.py               # Pydantic v2 models for every YAML schema
│   ├── validators.py            # 11 graph validators + compliance checks
│   ├── classifier.py            # Splits spec into System Prompt vs Reference Asset
│   ├── renderers.py             # Jinja2 → system_prompt.md, reference_asset.md/.json
│   ├── deduplicator.py          # Detects textually duplicate rules (report-only)
│   ├── mermaid_diagrams.py      # Exports Mermaid flowcharts to PNG/HTML
│   ├── mermaid_parser.py        # Parses Mermaid source → SubflowTemplate scaffolds
│   └── utils.py                 # Regex helpers for variables, constants, slots, targets
│
├── agents/                      # Agent definitions and shared assets
│   ├── defs/                    # Individual agent YAML definitions (one dir per agent_id)
│   └── shared/                  # Project-wide reusable YAML fragments
│       ├── tools/               # Tool declarations
│       ├── tool_contracts/      # Full I/O schemas for tools
│       ├── memory_slots/        # Slot definitions
│       ├── policies/            # Policy rule fragments
│       └── subflows/            # SubflowTemplate YAML files
│
├── profiles/                    # Channel and compliance profiles
│   ├── channels/
│   │   ├── voice.yaml
│   │   ├── chat.yaml
│   │   └── async_text.yaml
│   └── compliance/
│       └── medical_es.yaml
│
├── templates/
│   └── system_prompt.md.j2      # Jinja2 template for the System Prompt
│
├── main.py                      # Interactive TUI (Rich-based)
├── build_prompt.py              # Compilation CLI
└── scaffold_from_mermaid.py     # Mermaid → YAML scaffold generator
```

---

## CLI Reference

### `build_prompt.py` — Compile an agent

```
python build_prompt.py <config_dir> [options]
```

| Argument | Default | Description |
|---|---|---|
| `config_dir` | *(required)* | Path to the agent's config directory (e.g. `agents/defs/my_agent`) |
| `--channel` | `voice` | Channel profile: `voice`, `chat`, or `async_text` |
| `--verbosity` | `standard` | Output detail level: `minimal`, `standard`, or `verbose` |
| `--reference-formats` | `markdown json` | Reference asset formats to produce |
| `--compliance-profile` | *(none)* | Compliance rule set to apply (e.g. `medical_es`) |
| `--no-reference-asset` | `false` | Skip generating the reference asset entirely |
| `--fail-on-warnings` | `false` | Treat validation warnings as fatal errors |
| `--dist-dir` | `dist` | Root output directory |

**Exit codes:** `0` success, `1` validation/fatal error, `2` argument error.

Diagnostic reports are written on every run regardless of exit code.

---

### `scaffold_from_mermaid.py` — Generate YAML stubs from a diagram

```
python scaffold_from_mermaid.py <mermaid_file> <output_dir> [options]
```

| Argument | Default | Description |
|---|---|---|
| `mermaid_file` | *(required)* | Path to a `.mmd` file with a `flowchart TD` or `flowchart LR` diagram |
| `output_dir` | *(required)* | Directory to write the generated YAML files |
| `--agent-id` | *(required)* | Identifier used for naming the top-level YAML file |
| `--no-split-subgraphs` | `false` | Write everything to a single file instead of one per subgraph |

**Node shape → state type mapping:**

| Mermaid shape | State type |
|---|---|
| `ID[label]` | `message` |
| `ID{label}` | `decision` |
| `ID([label])` | `terminal` |
| `ID[[label]]` | `action` |
| `ID(label)` | `start` |

Routing and state IDs are pre-filled. Fields that require human authoring (`goal`, `say`, `capture`) are left as empty stubs.

---

### `main.py` — Interactive TUI

```
python main.py
```

A menu-driven terminal interface for:
- Compiling an agent (with optional configuration wizard)
- Generating Mermaid flowchart diagrams (full, root-only, or per subflow)
- Compiling and diagramming in one step
- Browsing output files from `dist/`

---

## Agent Config Files

Each agent lives in its own directory under `agents/defs/`. The directory must contain the following files:

| File | Purpose |
|---|---|
| `manifest.yaml` | Agent ID, start state, file includes, subflow instances |
| `constants.yaml` | Named constants referenced as `<NAME>` in rules and states |
| `input_variables.yaml` | Runtime variables injected as `{{name}}` at execution |
| `memory_slots.yaml` | Session memory slots referenced as `[name]` |
| `identity.yaml` | Agent role and identity statements |
| `objectives.yaml` | Primary objective, secondary objectives, success alternatives |
| `context.yaml` | Company context, approved services, process steps |
| `policies.yaml` | Dynamic policy sections (filtered by the active channel profile) |

The manifest additionally references include files (states, handlers, FAQs, rules) and shared assets from `agents/shared/`.

See [Agent Creation Guide](AGENT_CREATION_GUIDE.md) for detailed field documentation and examples.

---

## Shared Assets

Files in `agents/shared/` are available to any agent via manifest includes:

| Directory | YAML schema | Purpose |
|---|---|---|
| `agents/shared/tools/` | Tool declarations | Names and descriptions of callable tools |
| `agents/shared/tool_contracts/` | Tool contract schemas | Full input/output schemas for tools |
| `agents/shared/memory_slots/` | Slot definitions | Memory slot declarations reused across agents |
| `agents/shared/policies/` | Policy fragments | Rule blocks merged into agent policy sections |
| `agents/shared/subflows/` | SubflowTemplate files | Parametric subflows instantiated per agent |

---

## Profiles

### Channel profiles (`profiles/channels/`)

Each channel profile defines which policy sections exist, their display labels, and whether each section is required. Compile with `--channel voice|chat|async_text` to activate the corresponding profile.

### Compliance profiles (`profiles/compliance/`)

Compliance profiles add a registry of declarative rules (with severity levels) that are evaluated by the validator against the loaded `AgentSpec`. Use `--compliance-profile <id>` to activate.

---

## Output Artifacts

After a successful compilation, `dist/<agent_id>/` contains:

| File | Description |
|---|---|
| `full/system_prompt.md` | The agent's control logic — state machine, handlers, FAQs, policies |
| `full/system_prompt_mini.md` | Compact-notation companion (only with a `*_mini.md.j2` template) |
| `full/reference_asset.md` / `.json` | Static facts formatted for RAG retrieval |
| `split/system_prompt.md` | Deploy-platform profile fields: `# PERSONALITY` / `# GOAL` / `# INSTRUCTIONS` |
| `split/knowledge_base.md` (+ `_mini`) | The `CONVERSATION_FLOW` block, for platforms that index it separately |
| `split/reference_asset.md` / `.json` | Copy of the reference asset |
| `reports/validation_report.md` | All errors and warnings from the 11 graph validators |
| `reports/deduplication_report.md` | Groups of textually duplicate rules |
| `reports/orphan_states_report.md` | States that are defined but not reachable from start |

Reports are always written. `full/` and `split/` are written only when validation passes (no errors).

---

## Compilation Pipeline

The compiler runs seven deterministic stages in sequence:

1. **Load** — read YAML from disk, validate schemas, merge includes, resolve subflow instances
2. **Classify** — split spec fields into System Prompt content vs Reference Asset content
3. **Validate** — run 11 graph checks and any compliance rules; collect errors and warnings
4. **Deduplicate** — detect textually identical rules across all policy sources (report-only)
5. **Detect orphans** — find states unreachable from `start_at`
6. **Render** — fill Jinja2 template and produce markdown/JSON artifacts
7. **Write** — flush reports to disk; flush compiled artifacts only if validation passed

---

## State Machine DSL

The System Prompt contains a deterministic state machine DSL that the LLM runtime interprets:

**Token conventions:**
- `{{variable}}` — runtime-injected input variable
- `<CONSTANT>` — compile-time system constant
- `[slot]` — session memory slot (read/write)

**Execution order per user utterance:**
1. `GLOBAL_HANDLERS` — priority interrupts (cancel, operator, repeat, etc.)
2. `FAQ_POLICY` — semantic intent matching
3. Active state's `ROUTE` — conditional transitions
4. Active state's `FALLBACK` — default transition

**SAY block labels:**
- `[verbatim]` — the agent must say this text exactly as written
- `[flexible]` — the agent may paraphrase while preserving intent

---

## Contributing

1. All YAML config files are validated against Pydantic v2 schemas in `app/schemas.py`. Add or modify schemas there when introducing new fields.
2. New validators go in `app/validators.py`; they receive the fully merged `AgentSpec` and return a list of `ValidationIssue` objects.
3. New channel profiles are YAML files in `profiles/channels/`; new compliance profiles go in `profiles/compliance/`.
4. The Jinja2 template in `templates/system_prompt.md.j2` controls System Prompt layout — the classifier and renderer must stay in sync with it.
