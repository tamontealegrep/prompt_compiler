# Agent Creation Guide

This guide walks through every step required to define a new agent in the Prompt Compiler, from directory layout through compilation and validation. Use it as a reference when building agents in the future.

---

## Table of Contents

1. [Overview](#overview)
2. [Directory layout](#directory-layout)
3. [manifest.yaml](#manifestyaml)
4. [Core config files](#core-config-files)
   - [constants.yaml](#constantsyaml)
   - [input_variables.yaml](#input_variablesyaml)
   - [memory_slots.yaml](#memory_slotsyaml)
   - [identity.yaml](#identityyaml)
   - [objectives.yaml](#objectivesyaml)
   - [context.yaml](#contextyaml)
   - [policies.yaml](#policiesyaml)
5. [Include files](#include-files)
   - [states.yaml](#statesyaml)
   - [terminal_states.yaml](#terminal_statesyaml)
   - [handlers.yaml](#handlersyaml)
   - [faqs.yaml](#faqsyaml)
   - [flow_rules.yaml](#flow_rulesyaml)
   - [faq_policy.yaml](#faq_policyyaml)
6. [Shared assets](#shared-assets)
   - [tools and tool_contracts](#tools-and-tool_contracts)
   - [memory_slots (shared)](#memory_slots-shared)
   - [policies (shared)](#policies-shared)
   - [subflows (shared)](#subflows-shared)
7. [Subflow templates](#subflow-templates)
8. [State machine DSL reference](#state-machine-dsl-reference)
9. [Compiling the agent](#compiling-the-agent)
10. [Reading validation output](#reading-validation-output)
11. [Complete minimal example](#complete-minimal-example)

---

## Overview

An agent is defined entirely in YAML. The compiler reads those files, validates them, and produces:

- **`system_prompt.md`** — the agent's behavioral logic (state machine, handlers, FAQs, policies)
- **`reference_asset.md` / `.json`** — static facts optimized for RAG retrieval

The source of truth is always the YAML. Never edit the compiled output directly.

Token conventions used in YAML text fields:

| Token | Syntax | Resolved at |
|---|---|---|
| Input variable | `{{variable_name}}` | Runtime (injected by the caller) |
| System constant | `<CONSTANT_NAME>` | Compile time |
| Memory slot | `[slot_name]` | Runtime (session state) |

---

## Directory layout

Create a directory under `agents/defs/` named after your agent ID (no spaces, use underscores):

```
agents/defs/
└── my_agent/
    ├── manifest.yaml          # Required — root config
    ├── constants.yaml         # Required
    ├── input_variables.yaml   # Required
    ├── memory_slots.yaml      # Required
    ├── identity.yaml          # Required
    ├── objectives.yaml        # Required
    ├── context.yaml           # Required
    ├── policies.yaml          # Required
    ├── states.yaml            # Inline states (or use includes)
    ├── terminal_states.yaml   # Inline terminal states
    ├── handlers.yaml          # Inline global handlers
    ├── faqs.yaml              # Inline FAQ cards
    ├── flow_rules.yaml        # Procedural rules
    └── faq_policy.yaml        # FAQ matching rules
```

The manifest controls which files are loaded and in what order. Extra files not referenced in the manifest are ignored.

---

## manifest.yaml

The manifest is the root configuration file. It declares the agent ID, the entry state, all file includes, and subflow instantiations.

```yaml
agent_id: my_agent
start_at: WELCOME

# Optional — memory slots that can hold dynamic GO_TO targets
dynamic_state_slots: []

# Include files — paths relative to agents/defs/my_agent/
includes:
  handlers:
    - handlers.yaml
  states:
    - states.yaml
  terminal_states:
    - terminal_states.yaml
  faqs:
    - faqs.yaml
  flow_rules:
    - flow_rules.yaml
  faq_policy:
    - faq_policy.yaml
  tools:
    - ../../agents/shared/tools/my_tools.yaml      # shared tools
  tool_contracts:
    - ../../agents/shared/tool_contracts/my_contracts.yaml
  memory_slots:
    - ../../agents/shared/memory_slots/common.yaml # shared slots
  policies:
    - ../../agents/shared/policies/common_rules.yaml

# Subflow instances — each entry instantiates a SubflowTemplate
subflow_instances: []
```

**Key rules:**
- `agent_id` must match the directory name exactly.
- `start_at` must be a `state_id` that exists in one of the loaded state files.
- All paths in `includes` are relative to the agent config directory.
- Use `../../agents/shared/...` to reference project-level shared files.

---

## Core config files

### constants.yaml

Named constants are injected at compile time everywhere `<NAME>` appears in text fields.

```yaml
constants:
  - name: COMPANY_NAME
    value: "Acme Corp"
  - name: SUPPORT_PHONE
    value: "+1-800-555-0100"
  - name: MAX_RETRIES
    value: "3"
```

- Names must be `UPPER_SNAKE_CASE`.
- Values are always strings (wrap numbers in quotes).
- Use constants for values that are fixed but appear in multiple places — this prevents drift.

---

### input_variables.yaml

Input variables are injected by the caller at runtime. In text fields they appear as `{{variable_name}}`.

```yaml
input_variables:
  - name: customer_name
    description: "Full name of the customer"
    required: true
  - name: account_id
    description: "Customer account identifier"
    required: true
  - name: preferred_language
    description: "Language preference code (e.g. es, en)"
    required: false
```

- Names are `snake_case`.
- `required: true` variables are validated to be present before execution.
- Validators check that every `{{variable}}` used in states/handlers is declared here.

---

### memory_slots.yaml

Memory slots store session state. They are read and written by states using `[slot_name]` syntax.

```yaml
memory_slots:
  - name: collected_dob
    type: string
    description: "Customer date of birth collected during verification"
  - name: verification_passed
    type: boolean
    description: "Whether identity verification succeeded"
  - name: retry_count
    type: integer
    description: "Number of failed attempts on the current question"
  - name: next_destination
    type: state_id
    description: "Dynamic routing target — holds a state ID for GO_TO"
```

**Slot types:**

| Type | Description |
|---|---|
| `string` | Free text |
| `boolean` | `true` / `false` |
| `integer` | Whole number |
| `state_id` | Holds a state identifier — enables dynamic `GO_TO: [slot_name]` routing |

Slots with type `state_id` can be listed in `manifest.dynamic_state_slots` to unlock `GO_TO: [slot_name]` targets, which the validator type-checks.

---

### identity.yaml

A list of statements that define who the agent is. These populate the identity section of the System Prompt.

```yaml
identity:
  - "You are a virtual assistant for <COMPANY_NAME>."
  - "Your name is Alex."
  - "You communicate exclusively in Spanish with customers."
  - "You never reveal that you are an AI unless directly and explicitly asked."
```

Constants (`<NAME>`) are valid here. Keep statements declarative and factual.

---

### objectives.yaml

Defines the agent's core purpose and success criteria.

```yaml
primary_objective: >
  Guide the customer through account verification and route them to the
  appropriate service based on their needs.

secondary_objectives:
  - "Collect date of birth for identity verification."
  - "Determine the reason for the call."
  - "Transfer to a live agent when the customer requests it."

success_alternatives:
  - "Customer successfully verified and routed."
  - "Customer transferred to appropriate department."
  - "Customer opted to call back later — session ended cleanly."
```

---

### context.yaml

Static company and service information used by the Reference Asset.

```yaml
company_context: >
  Acme Corp is a financial services company specializing in personal loans
  and account management for retail customers.

approved_services:
  - name: "Personal Loan Application"
    description: "Customers can apply for personal loans up to $50,000."
  - name: "Account Balance Inquiry"
    description: "Customers can check their current balance and recent transactions."

summary_services_library:
  - "Personal Loan Application"
  - "Account Balance Inquiry"

approved_process_steps:
  - step: "Verify identity using date of birth."
  - step: "Confirm reason for call."
  - step: "Route to appropriate service or live agent."

support_and_trust:
  - "Always offer to transfer to a live agent if the customer is frustrated."
  - "Never promise outcomes that cannot be guaranteed."
```

---

### policies.yaml

Policy sections whose presence and naming are governed by the active channel profile. The compiler only includes sections that are defined in the channel profile.

```yaml
policies:
  # Section name must match the channel profile's defined sections exactly
  ESCALATION_POLICY:
    - "Transfer to a live agent if the customer requests it three times in a row."
    - "Escalate immediately if the customer mentions an emergency."

  ERROR_RECOVERY:
    - "If a tool call fails, apologize and offer an alternative."
    - "Do not retry a failed tool call more than once without informing the customer."

  SENSITIVE_DATA:
    - "Never repeat date of birth or account numbers back to the customer verbatim."
    - "Do not log or store full credit card numbers."
```

Run `--channel voice|chat|async_text` to select which profile governs which sections are required. Missing required sections cause a validation error.

---

## Include files

### states.yaml

The main state graph. Each state is a node in the conversation flow.

```yaml
states:
  - state_id: WELCOME
    type: message
    goal: "Greet the customer and introduce the agent."
    say: "[verbatim] Welcome to <COMPANY_NAME>. My name is Alex."
    route:
      - condition: "always"
        go_to: ASK_DOB

  - state_id: ASK_DOB
    type: question
    goal: "Ask the customer for their date of birth."
    say: "[flexible] To verify your identity, could you please provide your date of birth?"
    capture:
      slot: collected_dob
      type: string
    route:
      - condition: "[collected_dob] is not empty"
        go_to: VERIFY_IDENTITY
    fallback:
      go_to: ASK_DOB_RETRY

  - state_id: ASK_DOB_RETRY
    type: question
    goal: "Re-ask for date of birth after a failed attempt."
    say: "[verbatim] I'm sorry, I didn't catch that. Could you please repeat your date of birth?"
    capture:
      slot: collected_dob
      type: string
    route:
      - condition: "[collected_dob] is not empty"
        go_to: VERIFY_IDENTITY
      - condition: "[retry_count] >= <MAX_RETRIES>"
        go_to: TRANSFER_AGENT
    fallback:
      go_to: TRANSFER_AGENT

  - state_id: VERIFY_IDENTITY
    type: action
    goal: "Call the identity verification tool."
    execute: verify_customer_identity
    route:
      - condition: "[verification_passed] == true"
        go_to: ASK_REASON
      - condition: "[verification_passed] == false"
        go_to: VERIFICATION_FAILED
```

**State types and their required fields:**

| Type | `say` | `capture` | `execute` | `route` | `wait_for_input` |
|---|---|---|---|---|---|
| `start` | no | no | no | yes | no |
| `message` | yes | no | no | yes | no |
| `question` | yes | yes | no | yes | yes (implicit) |
| `decision` | no | no | no | yes | no |
| `action` | no | no | yes | yes | no |
| `registration` | yes | yes | no | yes | yes (implicit) |
| `subflow_change` | no | no | no | no | (handled by subflow) |
| `terminal` | optional | no | no | no | no |

**`say` field annotations:**
- `[verbatim]` — must be spoken exactly as written
- `[flexible]` — agent may paraphrase while preserving intent

---

### terminal_states.yaml

Terminal states end the conversation. They must have `type: terminal` and `final: true`.

```yaml
terminal_states:
  - state_id: END_SUCCESS
    type: terminal
    final: true
    say: "[verbatim] Thank you for calling <COMPANY_NAME>. Have a great day."

  - state_id: END_TRANSFER
    type: terminal
    final: true
    say: "[flexible] I will connect you with one of our agents now. Please hold."
```

---

### handlers.yaml

Global handlers are interrupts evaluated before state logic on every user utterance.

```yaml
handlers:
  - handler_id: HANDLER_CANCEL
    type: cancel
    trigger:
      - "I want to cancel"
      - "cancel this"
      - "never mind"
      - "forget it"
    say: "[flexible] Of course. Is there anything else I can help you with?"
    route:
      - condition: "always"
        go_to: END_SUCCESS

  - handler_id: HANDLER_OPERATOR
    type: operator
    trigger:
      - "I want to speak to an agent"
      - "give me a human"
      - "talk to a person"
      - "operator"
    say: "[verbatim] Understood. I will transfer you to one of our agents right away."
    route:
      - condition: "always"
        go_to: TRANSFER_AGENT

  - handler_id: HANDLER_REPEAT
    type: repeat
    trigger:
      - "can you repeat that"
      - "say that again"
      - "what did you say"
    say: "[verbatim] Of course, let me repeat that."
    route:
      - condition: "always"
        go_to: RESUME_CURRENT
```

**Execution order:** Handlers are evaluated first on every turn. First match wins. Use specific trigger phrases to avoid accidental matches.

---

### faqs.yaml

FAQ cards are semantic intent-matching entries evaluated after handlers.

```yaml
faqs:
  - faq_id: FAQ_HOURS
    match:
      - "what are your hours"
      - "when are you open"
      - "what time do you close"
      - "are you open on weekends"
    answer:
      - "[verbatim] Our customer service line is available Monday through Friday, 8 AM to 8 PM Eastern time."
    faq_resume_to: RESUME_CURRENT

  - faq_id: FAQ_PRIVACY
    match:
      - "what do you do with my data"
      - "how is my information used"
      - "is my data safe"
    answer:
      - "[flexible] Your privacy is very important to us. All information collected is stored securely and used only for account management purposes."
    faq_resume_to: RESUME_CURRENT
```

- `match` phrases define what the customer might say — the agent uses semantic matching, not exact string comparison.
- `faq_resume_to` sends the agent back to the state it was in before the FAQ was triggered.
- The validator checks for semantic duplicate match phrases across FAQs.

---

### flow_rules.yaml

Procedural rules applied to the main conversation flow. These appear in the System Prompt as ordered behavioral constraints.

```yaml
flow_rules:
  priority:
    - "Always complete identity verification before discussing account details."
    - "If the customer has called more than three times this week, offer to schedule a callback."
    - "Never proceed past ASK_DOB if [collected_dob] is empty."
```

---

### faq_policy.yaml

Rules governing how FAQ matching and fallback behavior work.

```yaml
faq_policy:
  - "If a question matches an FAQ with confidence above 80%, answer it immediately."
  - "If confidence is between 50% and 80%, ask a clarifying question before answering."
  - "If no FAQ matches, proceed with normal state logic."
  - "After answering an FAQ, resume the state the customer was in before the question."
```

---

## Shared assets

Files in `agents/shared/` are referenced via includes in the manifest using relative paths (`../../agents/shared/...`). They are merged at load time alongside agent-specific files.

### tools and tool_contracts

**`agents/shared/tools/my_tools.yaml`** — declares callable tools:

```yaml
tools:
  - name: verify_customer_identity
    description: "Verifies a customer's identity using their date of birth."
  - name: get_account_balance
    description: "Retrieves the current balance for a customer account."
```

**`agents/shared/tool_contracts/my_contracts.yaml`** — full I/O schemas for tools:

```yaml
tool_contracts:
  - name: verify_customer_identity
    inputs:
      - name: account_id
        type: string
        required: true
        description: "The customer account identifier."
      - name: date_of_birth
        type: string
        required: true
        description: "Date of birth in YYYY-MM-DD format."
    outputs:
      - name: verified
        type: boolean
        description: "True if identity was successfully verified."
    notes: "Returns false on any mismatch — do not reveal which field failed."

  - name: get_account_balance
    inputs:
      - name: account_id
        type: string
        required: true
    outputs:
      - name: balance
        type: number
        description: "Current account balance in USD."
      - name: currency
        type: string
        description: "Currency code (e.g. USD)."
    notes: "Returns null balance if account is suspended."
```

Tool names in `execute` fields of states must match exactly a name declared in `tools`. The validator enforces tool/contract consistency.

---

### memory_slots (shared)

Slot definitions reused across multiple agents:

**`shared/memory_slots/common.yaml`**:

```yaml
memory_slots:
  - name: session_id
    type: string
    description: "Unique identifier for the current session."
  - name: call_reason
    type: string
    description: "Primary reason for the customer's call."
```

These are merged with the agent-level `memory_slots.yaml`. Duplicate slot names across files cause a validation error.

---

### policies (shared)

Policy rule fragments merged into `policies.yaml`:

**`agents/shared/policies/common_rules.yaml`**:

```yaml
policies:
  ESCALATION_POLICY:
    - "Always offer escalation if the customer expresses frustration."
  DATA_PRIVACY:
    - "Never repeat sensitive data back to the customer."
```

Merged section-by-section with the agent's `policies.yaml`. Rules from shared files appear before agent-specific rules within each section.

---

### subflows (shared)

See the next section for the full SubflowTemplate specification.

---

## Subflow templates

A SubflowTemplate is a reusable flow defined in `agents/shared/subflows/`. It is instantiated in the manifest and can receive parameters that customize its behavior.

### Defining a subflow template

**`agents/shared/subflows/identity_verification.yaml`**:

```yaml
subflow_id: identity_verification
description: "Collects and verifies a customer's identity."

# Parameters this template accepts
params:
  - name: SUCCESS_TARGET
    description: "State to go to after successful verification."
  - name: FAILURE_TARGET
    description: "State to go to after failed verification."

# Memory slots scoped to this subflow instance
memory_slots:
  - name: dob_input
    type: string
  - name: verified
    type: boolean

start_at: ASK_DOB

states:
  - state_id: ASK_DOB
    type: question
    goal: "Collect date of birth."
    say: "[flexible] Please provide your date of birth."
    capture:
      slot: dob_input
      type: string
    route:
      - condition: "[dob_input] is not empty"
        go_to: RUN_VERIFY

  - state_id: RUN_VERIFY
    type: action
    goal: "Call verification tool."
    execute: verify_customer_identity
    route:
      - condition: "[verified] == true"
        go_to: "@self.SUCCESS"
      - condition: "[verified] == false"
        go_to: "@self.FAILURE"

# Exports — named aliases that callers use to reference targets inside this subflow
exports:
  SUCCESS: ASK_DOB    # internal target on success (overridden by param)
  FAILURE: ASK_DOB    # internal target on failure (overridden by param)
```

### Instantiating a subflow in the manifest

```yaml
subflow_instances:
  - instance_id: verify_flow
    template: identity_verification
    params:
      SUCCESS_TARGET: ASK_REASON    # main-flow state to go to on success
      FAILURE_TARGET: TRANSFER_AGENT
    exports:
      SUCCESS: "{{SUCCESS_TARGET}}"
      FAILURE: "{{FAILURE_TARGET}}"
```

### Referencing a subflow from a state

```yaml
- state_id: START_VERIFICATION
  type: subflow_change
  go_to: "@verify_flow.entry"   # @instance_id.export_name
```

**Namespace rules:**
- All state IDs inside the instance are prefixed as `INSTANCE_ID__STATE_ID` internally.
- All slot names inside the instance are prefixed as `instance_id__slot_name`.
- External states reference subflow states only via `@instance.export_name` aliases.
- This prevents ID collisions between subflows and the main flow.

---

## State machine DSL reference

### ROUTE and FALLBACK

```yaml
route:
  - condition: "{{account_type}} == premium"
    go_to: PREMIUM_FLOW
  - condition: "[retry_count] >= 3"
    go_to: TRANSFER_AGENT
  - condition: "[collected_dob] is not empty"
    go_to: VERIFY_IDENTITY

fallback:
  go_to: ASK_AGAIN
```

- `route` is a list of conditions evaluated top-to-bottom; first match wins.
- `fallback` is the default when no route condition matches.
- A state should have either `route` or `fallback` (or both), but the validator will warn about redundant configurations.

### Dynamic routing

A state can route based on a slot value rather than a static state ID:

```yaml
# In memory_slots.yaml:
- name: next_step
  type: state_id

# In manifest.yaml:
dynamic_state_slots:
  - next_step

# In a state:
route:
  - condition: "always"
    go_to: "[next_step]"   # resolves to whatever state ID is stored in the slot
```

The validator type-checks that slots used in `GO_TO: [slot]` form have type `state_id` and are declared in `dynamic_state_slots`.

### Question self-loops

When a question state routes back to itself (retry), the validator requires a retry counter to prevent infinite loops:

```yaml
- state_id: ASK_PHONE
  type: question
  say: "[flexible] What is your phone number?"
  capture:
    slot: phone_number
    type: string
  route:
    - condition: "[phone_number] is not empty"
      go_to: NEXT_STATE
    - condition: "[retry_count] < <MAX_RETRIES>"
      go_to: ASK_PHONE          # self-loop — retry_count must be tracked
  fallback:
    go_to: TRANSFER_AGENT
```

---

## Compiling the agent

Once all YAML files are in place, compile with:

```bash
# Basic compilation — voice channel, standard verbosity
python app/build_prompt.py agents/defs/my_agent --channel voice

# Full options
python app/build_prompt.py agents/defs/my_agent \
  --channel voice \
  --verbosity verbose \
  --reference-formats markdown json \
  --compliance-profile medical_es \
  --fail-on-warnings \
  --split-subflows \
  --dist-dir dist
```

### Starting from a Mermaid diagram

If you have a flowchart, generate YAML stubs first:

```bash
# 1. Write your diagram to a .mmd file
# 2. Scaffold YAML
python scaffold_from_mermaid.py diagram.mmd agents/defs/my_agent --agent-id my_agent

# 3. Fill in the stubs (goal, say, capture fields)
# 4. Compile
python app/build_prompt.py agents/defs/my_agent --channel voice
```

The scaffolder pre-fills `state_id`, `type`, and `route` from the diagram. You must add `goal`, `say`, and `capture` manually.

### Using the TUI

```bash
python main.py
```

The TUI guides you through selecting a config, configuring options, running compilation, and viewing output — without memorizing CLI flags.

---

## Reading validation output

After compilation, check `dist/my_agent/reports/validation_report.md`. The report lists:

- **ERRORS** — must be fixed; compilation output is not written
- **WARNINGS** — should be reviewed; output is written unless `--fail-on-warnings` is set

Common errors and their fixes:

| Error | Likely cause | Fix |
|---|---|---|
| `Dangling GO_TO target: STATE_X` | `go_to: STATE_X` references a state that doesn't exist | Add the state or fix the typo |
| `Duplicate state_id: ASK_DOB` | Same `state_id` appears in two files | Rename one of them |
| `Unreachable state: VERIFY_IDENTITY` | No other state points to this one | Add a route to it or delete it |
| `Cycle detected involving: [A, B, C]` | States form a loop with no terminal exit | Add a terminal branch to one of the loop states |
| `Tool 'verify_x' not found in tool_contracts` | Tool declared but no contract schema | Add entry to the tool contracts file |
| `Variable {{name}} used but not declared` | Missing entry in `input_variables.yaml` | Declare the variable |
| `Slot [name] used but not declared` | Missing entry in `memory_slots.yaml` | Declare the slot |
| `Required policy section missing: ESCALATION_POLICY` | Channel profile requires a section not in `policies.yaml` | Add the section |

---

## Complete minimal example

The following is the smallest valid agent — a single-question flow that greets the user and transfers them.

**`agents/defs/minimal_agent/manifest.yaml`**
```yaml
agent_id: minimal_agent
start_at: WELCOME
includes:
  states:
    - states.yaml
  terminal_states:
    - terminal_states.yaml
  handlers:
    - handlers.yaml
subflow_instances: []
```

**`agents/defs/minimal_agent/constants.yaml`**
```yaml
constants:
  - name: COMPANY_NAME
    value: "Acme Corp"
```

**`agents/defs/minimal_agent/input_variables.yaml`**
```yaml
input_variables:
  - name: customer_name
    description: "Customer full name"
    required: true
```

**`agents/defs/minimal_agent/memory_slots.yaml`**
```yaml
memory_slots:
  - name: call_reason
    type: string
```

**`agents/defs/minimal_agent/identity.yaml`**
```yaml
identity:
  - "You are a virtual assistant for <COMPANY_NAME>."
```

**`agents/defs/minimal_agent/objectives.yaml`**
```yaml
primary_objective: "Greet the customer and transfer them to the right team."
secondary_objectives: []
success_alternatives: []
```

**`agents/defs/minimal_agent/context.yaml`**
```yaml
company_context: "Acme Corp provides customer service."
approved_services: []
summary_services_library: []
approved_process_steps: []
support_and_trust: []
```

**`agents/defs/minimal_agent/policies.yaml`**
```yaml
policies: {}
```

**`agents/defs/minimal_agent/states.yaml`**
```yaml
states:
  - state_id: WELCOME
    type: message
    goal: "Greet the customer."
    say: "[verbatim] Welcome to <COMPANY_NAME>, {{customer_name}}. How can I help you today?"
    route:
      - condition: "always"
        go_to: ASK_REASON

  - state_id: ASK_REASON
    type: question
    goal: "Find out why the customer called."
    say: "[flexible] What can I help you with today?"
    capture:
      slot: call_reason
      type: string
    route:
      - condition: "[call_reason] is not empty"
        go_to: TRANSFER
    fallback:
      go_to: ASK_REASON
```

**`agents/defs/minimal_agent/terminal_states.yaml`**
```yaml
terminal_states:
  - state_id: TRANSFER
    type: terminal
    final: true
    say: "[verbatim] Let me connect you with the right team. Please hold."
```

**`agents/defs/minimal_agent/handlers.yaml`**
```yaml
handlers:
  - handler_id: HANDLER_CANCEL
    type: cancel
    trigger:
      - "cancel"
      - "never mind"
      - "goodbye"
    say: "[verbatim] Thank you for calling. Goodbye."
    route:
      - condition: "always"
        go_to: END_GOODBYE
```

> Add a `END_GOODBYE` terminal state or adjust the handler to route to `TRANSFER`.

**Compile:**
```bash
python app/build_prompt.py agents/defs/minimal_agent --channel chat --verbosity minimal
```
