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

- **`full/system_prompt.md`** — the agent's behavioral logic (state machine, handlers, FAQs, policies)
- **`full/reference_asset.md` / `.json`** — static facts optimized for RAG retrieval
- **`split/`** — a re-slice of the same prompt for hosting platforms that cap the profile field: `system_prompt.md` (`# PERSONALITY` / `# GOAL` / `# INSTRUCTIONS`) plus `knowledge_base.md` (the `CONVERSATION_FLOW` block)

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
    say:
      - "Welcome to <COMPANY_NAME>. My name is Alex."
    say_verbatim: true
    route:
      - condition: "always"
        go_to: ASK_DOB

  - state_id: ASK_DOB
    type: question
    goal: "Ask the customer for their date of birth."
    say:
      - "To verify your identity, could you please provide your date of birth?"
    say_verbatim: false
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
    say:
      - "I'm sorry, I didn't catch that. Could you please repeat your date of birth?"
    say_verbatim: true
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

**`say_verbatim` flag:**
- `true` → rendered as `[verbatim]` — must be spoken exactly as written
- `false` (default) → rendered as `[flexible]` — agent may paraphrase while preserving intent

---

### terminal_states.yaml

Terminal states end the conversation. They must have `type: terminal` and `final: true`.

```yaml
terminal_states:
  - state_id: END_SUCCESS
    type: terminal
    final: true
    say:
      - "Thank you for calling <COMPANY_NAME>. Have a great day."
    say_verbatim: true

  - state_id: END_TRANSFER
    type: terminal
    final: true
    say:
      - "I will connect you with one of our agents now. Please hold."
    say_verbatim: false
```

---

### handlers.yaml

Global handlers are interrupts evaluated before state logic on every user utterance.

```yaml
handlers:
  - handler_id: HANDLER_CANCEL
    type: message
    wait: "no"
    trigger:
      - "I want to cancel"
      - "cancel this"
      - "never mind"
      - "forget it"
    say:
      - "Of course. Is there anything else I can help you with?"
    say_verbatim: false
    route:
      - "GO_TO: END_SUCCESS"

  - handler_id: HANDLER_OPERATOR
    type: message
    wait: "no"
    trigger:
      - "I want to speak to an agent"
      - "give me a human"
      - "talk to a person"
      - "operator"
    say:
      - "Understood. I will transfer you to one of our agents right away."
    say_verbatim: true
    route:
      - "GO_TO: TRANSFER_AGENT"

  - handler_id: HANDLER_REPEAT
    type: message
    wait: "no"
    trigger:
      - "can you repeat that"
      - "say that again"
      - "what did you say"
    say:
      - "Of course, let me repeat that."
    say_verbatim: true
    route:
      - "GO_TO: [current_state]"
```

**Execution order:** Handlers are evaluated first on every turn. First match wins. Use specific trigger phrases to avoid accidental matches.

---

### faqs.yaml

FAQ cards are semantic intent-matching entries evaluated after handlers.

```yaml
faqs:
  - faq_id: FAQ_HOURS
    type: message
    match:
      - "what are your hours"
      - "when are you open"
      - "what time do you close"
      - "are you open on weekends"
    say:
      - "Our customer service line is available Monday through Friday, 8 AM to 8 PM Eastern time."
    say_verbatim: true
    resume_to: "[current_state]"

  - faq_id: FAQ_PRIVACY
    type: message
    match:
      - "what do you do with my data"
      - "how is my information used"
      - "is my data safe"
    say:
      - "Your privacy is very important to us. All information collected is stored securely and used only for account management purposes."
    say_verbatim: false
    resume_to: "[current_state]"
```

- `match` phrases define what the customer might say — the agent uses semantic matching, not exact string comparison.
- `say` contains the response lines the agent should deliver, mirroring the same field used on handlers and states.
- `say_verbatim` controls how the renderer annotates the SAY block: `true` → `[verbatim]` (literal, must not be paraphrased), `false` → `[flexible]` (paraphrasable). Defaults to `false`.
- `resume_to` is an optional routing hint telling the agent where to return after answering the FAQ (e.g. `"[current_state]"` to resume where it left off). Omit if no resume routing is needed.
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
    description: "Authorized tool to verify a customer identity from account and birth-date data."
    notes: "Keep notes immediately after description when the runtime tool UI only exposes one instructions field and you need to copy description + notes together."
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

  - name: get_account_balance
    description: "Authorized tool to retrieve the current balance for a customer account."
    notes: "Keep notes immediately after description when the runtime tool UI only exposes one instructions field and you need to copy description + notes together."
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
```

Tool names in `execute` fields of states must match exactly a name declared in `tools`. The validator enforces tool/contract consistency.

#### Recommended LLM-oriented tool contract pattern

For tools that must be invoked reliably from `action` nodes, prefer a contract shape that removes ambiguity about whether the model should call the tool or reason past it.

Recommended structure:

```yaml
tool_contracts:
  - name: authoritative_tool
    description: "Authorized tool for <job>. Call it immediately when the flow reaches the point where <job> must be resolved. Its output is the only authorized source of truth for the next decision."
    notes:
      - "Keep notes immediately after description when the runtime tool UI only exposes one instructions field and you need to copy description + notes together."
      - "MANDATORY EXECUTION TRIGGER: call authoritative_tool as the next assistant action when <trigger condition>."
      - "ABSOLUTE EXECUTION RULE: do not answer, summarize, confirm, infer, or continue before the tool call completes."
      - "ARGUMENT RULE: build arguments only from declared slots, variables, constants, or the latest tool output. Do not invent missing values."
      - "SOURCE OF TRUTH RULE: use result_field as the only authorized post-tool result. Do not replace it with model reasoning."
      - "POST-EXECUTION RULE: if the output is null, invalid, false, stale, or not traceable to this call, stay in the recovery branch defined by the flow."
    inputs:
      - name: required_input
        required: true
        description: "Exact value to send. State where it comes from and which transformations are allowed."
    outputs:
      - name: result_field
        required: true
        description: "Canonical output field used by the flow after execution."
```

Recommended ordering in `tool_contracts.yaml`:

```yaml
- name: tool_name
  description: "..."
  notes:
    - "..."
  inputs:
    - name: input_name
      required: true
      description: "..."
  outputs:
    - name: output_name
      required: true
      description: "..."
```

Put `notes` immediately after `description` whenever the deployment runtime exposes a single instructions field and operators need to copy the contract's instruction-bearing text in one pass.

Why this works better for LLM agents:

- It tells the model exactly when tool execution becomes mandatory.
- It separates pre-call rules, argument construction, and post-call interpretation.
- It names one canonical output field instead of forcing the model to reinterpret prose.
- It reduces the chance that the model treats a tool as optional background guidance.

Anti-patterns to avoid:

- Mixing "call the tool now" with "ask the user before calling" inside the same `action`-stage contract.
- Describing multiple possible output shapes for the same tool result.
- Letting the contract imply the model may estimate, derive, or summarize the answer without executing the tool.
- Referring to undocumented arguments, stale prior availability, or hidden platform memory as if they were safe sources of truth.

#### Pre-deployment checklist for `action` nodes and tool contracts

Use this checklist before deploying any new agent or any change that affects a tool-driven flow.

**Tool contract review**

- The tool name matches exactly across `tools.yaml`, `tool_contracts.yaml`, and every `execute` field that references it.
- The contract description states clearly that the tool is authorized for a specific job and whether the call is mandatory.
- Every input field names its source unambiguously: slot, runtime variable, constant, or latest tool output.
- Every input field states any allowed transformation explicitly, such as ISO normalization, unit conversion, or literal mapping.
- The contract exposes one canonical output field or one canonical verdict representation for downstream routing.
- The notes include a `MANDATORY EXECUTION TRIGGER` when the tool must be called from a specific flow point.
- The notes include an `ABSOLUTE EXECUTION RULE` when the assistant must not speak, confirm, infer, or continue before the tool call completes.
- The notes include an `ARGUMENT RULE` that forbids inventing missing values or sourcing arguments from undeclared memory.
- The notes include a `SOURCE OF TRUTH RULE` that makes clear which output field governs the next decision.
- The notes include a `POST-EXECUTION RULE` that explains what to do if the tool returns null, false, stale, invalid, or incomplete data.
- The contract does not describe multiple incompatible output shapes for the same result.
- The contract does not mix platform behavior with conversational fallback instructions that belong in the flow.
- The contract does not imply that the model may estimate the answer instead of calling the tool.
- Constant or platform-fixed arguments described in the real runtime are reflected accurately in the contract text.
- If the runtime tool contract differs from the compiler repo contract, that drift is resolved before deployment.

**`action` node review**

- The node type is really `action`, not `decision` or `registration` with hidden tool behavior in `do` text.
- The node goal states that the tool call is obligatory and that the next valid assistant action is the real tool call.
- The node goal states what captured result must exist before the flow may continue.
- The `do` block starts with a direct imperative such as `TOOL CALL ONLY: call <tool_name> now.`
- The `do` block explicitly forbids user-facing speech before the tool call when the node is non-conversational.
- The `do` block explicitly forbids FAQ handling, handler continuation, or branch progression before the tool result is captured when that matters.
- The `do` block maps each argument from concrete slots, variables, constants, or previous tool outputs.
- The `do` block identifies one canonical output as the only authorized source of truth for the next step.
- The node does not contain instructions to ask the user for clarification unless that clarification is captured in an earlier question node.
- The `capture` field matches the actual output shape expected from the tool.
- Any `store` rule derived from the tool result is explicit and only runs after a valid result is captured.
- The `route` block advances only on a real captured result, not on an assumed outcome.
- The `fallback` block keeps the agent in the safe recovery path and does not silently skip the tool call.
- The node wording does not invite the model to summarize, infer, or replace the tool result with reasoning.
- If the tool is safety-critical or business-critical, the node repeats that the tool result is mandatory and authoritative.

**Flow-level consistency review**

- Every `action` node has a preceding path that guarantees required arguments are already captured before entry.
- If a tool depends on normalized dates, mapped literals, or prior tool outputs, that preparation happens before the node or is fully specified inside the node without requiring extra conversation.
- No later node contradicts the tool contract by reinterpreting the output in a different shape.
- Recovery branches after tool failure route back to the correct capture or retry point instead of continuing with stale state.
- Adjacent message or decision nodes do not accidentally weaken the tool's authority by offering inferred results.

**Deployment gate**

- Rebuild the affected agent and confirm validation reports show `0 errors` and `0 warnings`.
- Inspect the rendered `full/system_prompt_mini.md` to ensure the `ACT` nodes still read as imperative execution steps rather than descriptive guidance.
- Inspect the rendered `full/reference_asset.md` to ensure the contract wording remained canonical and readable after compilation.
- If possible, run at least one transcript or simulation that reaches each changed `action` node and verify the model actually calls the tool instead of hallucinating the answer.

#### Short PR checklist

Use this version in pull requests or review comments when you need a fast gate.

- Tool names match across `tools.yaml`, `tool_contracts.yaml`, and all `execute` references.
- Each tool contract defines one canonical output or verdict shape.
- Each mandatory tool includes trigger, anti-hallucination, source-of-truth, and post-execution rules.
- Each `action` node says the next valid assistant action is the real tool call.
- Each `action` node maps arguments from declared slots, variables, constants, or previous tool outputs only.
- No `action` node mixes tool execution with new conversational clarification that should happen earlier in the flow.
- `route` and `fallback` do not let the flow advance on an assumed tool result.
- Recompiled artifacts are clean and the rendered `ACT` nodes still read as imperative execution instructions.

#### Patterns and anti-patterns for `action` nodes

Use these as design heuristics when writing or reviewing tool-driving states.

**Recommended patterns**

- Pattern: state that the next valid assistant action is the real tool call in the current turn.
- Pattern: name one canonical captured output that gates all downstream routing.
- Pattern: start the `do` block with a hard imperative such as `TOOL CALL ONLY: call <tool_name> now.`
- Pattern: map every tool argument from declared slots, runtime variables, constants, or prior tool outputs.
- Pattern: explicitly forbid user-facing speech before the call when the node is non-conversational.
- Pattern: explicitly forbid FAQ handling, handler drift, or normal route progression before the tool result is captured when that matters.
- Pattern: repeat that the tool result is authoritative when the node supports a business-critical or safety-critical decision.
- Pattern: keep all needed clarification in earlier `question` nodes so the `action` node can stay execution-only.
- Pattern: route forward only on a real captured result and loop or fail safely otherwise.
- Pattern: make the fallback preserve tool authority, usually by retrying the tool node or returning to the last legitimate capture point.

**Common anti-patterns**

- Anti-pattern: describing the tool as helpful or recommended instead of mandatory when the flow actually depends on it.
- Anti-pattern: saying the assistant may answer directly if it already "knows" enough.
- Anti-pattern: mixing tool execution with fresh conversational tasks like "ask the user if needed" inside the same `action` node.
- Anti-pattern: allowing the node to continue to the next state without checking for a real captured tool result.
- Anti-pattern: using vague outputs like `result` when the rest of the flow expects a more specific semantic verdict.
- Anti-pattern: letting the `do` block describe several possible output interpretations instead of one canonical one.
- Anti-pattern: referencing undeclared memory, hidden platform context, or stale previous tool results as valid argument sources.
- Anti-pattern: instructing the model to estimate, infer, summarize, or approximate the tool answer if the call fails.
- Anti-pattern: hiding essential argument mapping only in the tool contract and not restating it in the `action` node when the mapping is non-trivial.
- Anti-pattern: writing a fallback that silently skips the tool call and advances the flow as if the action had succeeded.

#### Mini template: `action` node YAML scaffold

Use this scaffold as a copy-paste starting point for nodes that must call a tool reliably.

```yaml
- state_id: ACTION_STATE_ID
  type: action
  goal:
    - "Mandatory action: execute tool_name as soon as this state is entered."
    - "The next valid assistant action in this turn is the real tool call to tool_name."
    - "Do not continue until a real result_slot has been captured."
  do:
    - "TOOL CALL ONLY: call tool_name now."
    - "Map [slot_a] to input_a and [slot_b] to input_b."
    - "Use result_slot as the only authorized result for the next route decision."
    - "Do not answer the user, do not execute FAQs or handlers, and do not continue before the tool result is captured."
    - "If the tool is not executed or [result_slot] remains null, stay in this recovery path and do not infer the result."
  wait: "no"
  capture:
    - slot: result_slot
      type_expr: "string"
  store: []
  execute: tool_name
  route:
    - "IF [result_slot] IS NOT NULL -> GO_TO: NEXT_STATE"
  fallback:
    - "GO_TO: ACTION_STATE_ID"
```

Adaptation notes:

- Replace `type_expr` with the real output type expected from the tool.
- Add a `store` rule only when the tool result must be copied or normalized into another slot.
- Replace the self-loop fallback when the safe recovery path should return to an earlier capture state instead.
- If argument mapping is non-trivial, state the transformation explicitly inside `do` rather than assuming it from the contract alone.

#### Snippet library for common `action` node shapes

Use these variants when the base scaffold is too generic.

**Boolean result snippet**

Use this shape when the tool returns a primitive yes/no, success/failure, or true/false result.

```yaml
- state_id: ACTION_BOOLEAN_RESULT
  type: action
  goal:
    - "Mandatory action: execute tool_name as soon as this state is entered."
    - "The next valid assistant action in this turn is the real tool call to tool_name."
    - "Do not continue until a real boolean_result has been captured."
  do:
    - "TOOL CALL ONLY: call tool_name now."
    - "Map declared inputs only from slots, variables, constants, or previous tool outputs."
    - "Use boolean_result as the only authorized result for the next route decision."
    - "Do not answer the user and do not continue before the tool result is captured."
  wait: "no"
  capture:
    - slot: boolean_result
      type_expr: "boolean"
  execute: tool_name
  route:
    - "IF [boolean_result] == true -> GO_TO: SUCCESS_STATE"
  fallback:
    - "GO_TO: FAILURE_OR_RETRY_STATE"
```

**List result snippet**

Use this shape when the tool returns options, candidates, slots, or any collection that the next node must present or filter.

```yaml
- state_id: ACTION_LIST_RESULT
  type: action
  goal:
    - "Mandatory action: execute tool_name as soon as this state is entered."
    - "The next valid assistant action in this turn is the real tool call to tool_name."
    - "Do not continue until a real list_result has been captured."
  do:
    - "TOOL CALL ONLY: call tool_name now."
    - "Use list_result as the only authorized source of options for all downstream messages and decisions."
    - "Do not mention options, dates, times, or alternatives before the tool result is captured."
    - "If the tool returns an empty, invalid, stale, or untraceable list, do not invent options. Route to the configured retry or fallback path."
  wait: "no"
  capture:
    - slot: list_result
      type_expr: "list"
  execute: tool_name
  route:
    - "IF [list_result] IS NOT NULL -> GO_TO: NEXT_PRESENTATION_STATE"
  fallback:
    - "GO_TO: EMPTY_OR_RETRY_STATE"
```

**Textual verdict snippet**

Use this shape when the tool returns a semantic verdict string such as `Apta`, `Rejected`, `Inconclusive`, or any canonical classification label.

```yaml
- state_id: ACTION_TEXT_VERDICT
  type: action
  goal:
    - "Mandatory action: execute tool_name as soon as this state is entered."
    - "The next valid assistant action in this turn is the real tool call to tool_name."
    - "Do not continue until a real verdict_result has been captured."
  do:
    - "TOOL CALL ONLY: call tool_name now."
    - "Map all currently available inputs from declared slots and send missing values as empty only if the contract allows it."
    - "Use verdict_result as the only authorized semantic verdict for downstream routing."
    - "Do not classify, summarize, or infer the verdict before the tool result is captured."
  wait: "no"
  capture:
    - slot: verdict_result
      type_expr: "string"
  execute: tool_name
  route:
    - "IF [verdict_result] == 'APPROVED' -> GO_TO: APPROVED_STATE"
    - "IF [verdict_result] == 'INCONCLUSIVE' -> GO_TO: RECOVER_MISSING_DATA_STATE"
  fallback:
    - "GO_TO: REJECTED_OR_RETRY_STATE"
```

**Composite result snippet (`has_flag + payload_list`)**

Use this shape when the tool returns a control flag together with a payload list, for example `has_availability + available_slots`.

```yaml
- state_id: ACTION_COMPOSITE_RESULT
  type: action
  goal:
    - "Mandatory action: execute tool_name as soon as this state is entered."
    - "The next valid assistant action in this turn is the real tool call to tool_name."
    - "Do not continue until the control flag and payload list from the real tool response are available."
  do:
    - "TOOL CALL ONLY: call tool_name now."
    - "Capture payload_list from the tool response and evaluate control_flag only from that same response."
    - "Use control_flag and [payload_list] together as the only authorized source of truth for downstream routing and presentation."
    - "Do not mention options, dates, times, candidates, or alternatives before the tool result is captured."
    - "If control_flag is false, or if [payload_list] is null, empty, stale, invalid, or untraceable to this call, route to the configured no-data or retry path and do not invent payload items."
  wait: "no"
  capture:
    - slot: payload_list
      type_expr: "list"
  execute: tool_name
  route:
    - "IF tool_name.control_flag == true AND [payload_list] IS NOT NULL -> GO_TO: NEXT_PRESENTATION_STATE"
  fallback:
    - "GO_TO: EMPTY_OR_RETRY_STATE"
```

Adapt this pattern when the platform exposes part of the tool response directly in routing conditions and another part through captured slots.

#### Example: good `action` node

```yaml
- state_id: VERIFY_ELIGIBILITY
  type: action
  goal:
    - "Mandatory action: execute verify_eligibility as soon as this state is entered."
    - "The next valid assistant action in this turn is the real tool call to verify_eligibility."
    - "Do not continue until a real eligibility_result has been captured."
  do:
    - "TOOL CALL ONLY: call verify_eligibility now."
    - "Map [age] to age and [city] to city."
    - "Use eligibility_result as the only authorized verdict for the next route decision."
    - "Do not answer the user, do not execute FAQs or handlers, and do not continue before the tool result is captured."
  capture:
    - slot: eligibility_result
      type_expr: "string"
  execute: verify_eligibility
  route:
    - "IF [eligibility_result] IS NOT NULL -> GO_TO: NEXT_STEP"
  fallback:
    - "GO_TO: VERIFY_ELIGIBILITY"
```

Why it is good:

- The node makes tool execution mandatory in the current turn.
- It identifies one canonical result field.
- It maps arguments from declared state.
- It blocks conversational drift before the call completes.
- It does not allow routing on an assumed outcome.

#### Example: bad `action` node

```yaml
- state_id: VERIFY_ELIGIBILITY
  type: action
  goal:
    - "Check whether the user might be eligible."
  do:
    - "If you already know enough, you can answer directly."
    - "Otherwise call verify_eligibility or ask the user for any extra detail you need."
    - "If the tool fails, estimate the most likely result and continue."
  capture:
    - slot: result
      type_expr: "string"
  execute: verify_eligibility
  route:
    - "GO_TO: NEXT_STEP"
  fallback: []
```

Why it is bad:

- It treats the tool call as optional.
- It invites the model to answer from its own reasoning.
- It mixes execution with fresh clarification that belongs in earlier question nodes.
- It lacks a canonical guarded route based on a real captured result.
- It allows the flow to continue even if the tool was never called.

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

# Tools this template requires — validated at instantiation against tools.yaml and tool_contracts.yaml
required_tools:
  - verify_customer_identity

# Constants this template requires — validated at instantiation against constants.yaml
required_constants:
  - MAX_VERIFY_RETRIES

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
    say:
      - "Please provide your date of birth."
    say_verbatim: false
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
- Style rule: when a state has one or more conditional `route` entries plus one default transition, keep the conditional branches in `route` and place the default unconditional `GO_TO` in `fallback` instead of as the last `route` item.
- Prefer a final unconditional `GO_TO` inside `route` only when the state truly has no conditional routing and the whole state is a single direct transition.

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
  say:
    - "What is your phone number?"
  say_verbatim: false
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
    say:
      - "Welcome to <COMPANY_NAME>, {{customer_name}}. How can I help you today?"
    say_verbatim: true
    route:
      - condition: "always"
        go_to: ASK_REASON

  - state_id: ASK_REASON
    type: question
    goal: "Find out why the customer called."
    say:
      - "What can I help you with today?"
    say_verbatim: false
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
    say:
      - "Let me connect you with the right team. Please hold."
    say_verbatim: true
```

**`agents/defs/minimal_agent/handlers.yaml`**
```yaml
handlers:
  - handler_id: HANDLER_CANCEL
    type: message
    wait: "no"
    trigger:
      - "cancel"
      - "never mind"
      - "goodbye"
    say:
      - "Thank you for calling. Goodbye."
    say_verbatim: true
    route:
      - "GO_TO: END_GOODBYE"
```

> Add a `END_GOODBYE` terminal state or adjust the handler to route to `TRANSFER`.

**Compile:**
```bash
python app/build_prompt.py agents/defs/minimal_agent --channel chat --verbosity minimal
```