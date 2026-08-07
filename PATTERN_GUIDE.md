# Pattern Guide: Compact Node Design for YAML Flows

## Purpose

This document describes a design pattern for building YAML flows that are more compact, easier to maintain, and shorter in the final compiled prompt.

The main goals are:

- reduce the total number of nodes,
- eliminate intermediate registration nodes that only replicate data,
- centralize retry logic in decision nodes,
- keep the original functionality intact,
- leave user-facing messages untouched.

This pattern has been applied and validated across multiple production-style flows in this project — identity-verification/opening flows, multi-step qualification questionnaires, and tool-driven scheduling flows among them.

---

## Core idea

The traditional way to model a question in these flows tends to be:

1. a `question` node to ask,
2. a `registration` node to store the answer and bump a retry counter,
3. a `decision` node to validate the value, decide the next step, and retry if needed.

The optimized pattern eliminates the intermediate `registration` node whenever it doesn't add a real, distinct function.

### Recommended compact form

The sequence becomes:

1. `start` or the previous node,
2. `question` with `capture` and `store`,
3. `decision` with:
   - evaluation of the captured value,
   - increment of the retry counter,
   - a cutoff route when `<MAX_RETRY_ATTEMPTS>` is reached,
   - retry routes when the value is `NULL` or unparseable,
   - definitive exit routes when the value is valid.

---

## What problem this solves

In many flows, the user answers a question and the YAML then does two separate things:

- one node just to copy the captured value into a slot,
- another node just to bump the counter,
- another node to decide.

That produces:

- more nodes in the graph,
- more text in the compiled prompt,
- more semantic repetition,
- more noise when reading and maintaining the flow.

The proposed pattern compresses that structure into the smallest number of nodes possible without losing control.

---

## Pattern principles

### 1. The question should capture and persist the data directly

The `question` node should include:

- `capture` with the correct type,
- `store` with the direct assignment to the local slot.

Conceptual example:

```yaml
- state_id: X_ASK_FOO
  type: question
  say:
    - "..."
  wait: "yes"
  capture:
    - slot: foo
      type_expr: "Literal[yes, no]"
  store:
    - "[foo] = [foo]"
  route:
    - "GO_TO: X_DECIDE_FOO"
```

### 2. The retry counter lives in the decision, not in an intermediate registration node

Instead of creating a `registration` node just to:

- store the answer,
- increment the counter,

move the increment into the `decision` node.

The decision should contain a line like:

```yaml
- "[foo_try] = [foo_try] + 1"
```

Always use an explicit assignment (`[slot] = [slot] + 1`) rather than a bare expression like `[slot] + 1` — the latter has no assignment operator and won't be recognized as a real increment by anything that scans the flow for slot assignments.

### 3. The decision is the sole owner of retry policy

Every meaningful question should have an explicit policy in its associated decision:

- if the value is `NULL`, ask again,
- if the value is invalid or unparseable, count it as a failed attempt,
- if the counter exceeds the maximum, redirect to a defined exit,
- if the value is valid, advance.

### 4. Avoid duplicated explanatory text in `do`

The `do` block of a decision should be short and operative.

Avoid repeating long phrases such as:

- "ask again",
- "evaluate the value",
- "if invalid, ask again",
- "increment the counter and ask again".

Recommendation:

- one line to evaluate,
- one line to increment the counter,
- nothing more.

### 5. Use deterministic exits

Every decision should have a clear outcome for:

- a valid value,
- a null value,
- the maximum number of attempts reached,
- a definitive negative condition.

No route should be left ambiguous or fall through implicitly without intent.

---

## Recommended structure by node type

### A. `start`

Should do exactly one thing:

- send to a counter-initialization node, or
- send to the first logical node of the flow if there are no counters to initialize.

Example:

```yaml
- state_id: FLOW_START
  type: start
  route:
    - "GO_TO: FLOW_INIT_RETRY_COUNTS"
```

### B. Initial `registration`

A single initialization node at the start of the flow is recommended when there are several counters (or several slots that need a known starting value).

It should:

- set every counter to zero (and any relevant slot to `NULL`),
- not ask the user anything,
- not decide anything,
- not contain any additional business logic.

Example:

```yaml
- state_id: FLOW_INIT_RETRY_COUNTS
  type: registration
  do:
    - "[a_try] = 0"
    - "[b_try] = 0"
```

### C. `question`

Should:

- express a single capture intent,
- capture the variable,
- persist the value in `store`,
- go straight to the corresponding decision.

Practical rule:

- if the node asks the user something and the data must be persisted, `store` belongs here.
- if the node captures nothing, don't invent an empty `store`.

### D. `decision`

Should:

- interpret the captured data,
- increment the retry counter,
- apply the attempt limit,
- resolve the next jump.

A decision doesn't always need a preceding question in the same turn — it can also branch purely on context that's already available (a runtime input variable, a previously captured slot, or a tool result), to skip asking something the flow already knows. See "Conditional-skip pattern" below.

General example:

```yaml
- state_id: FLOW_DECIDE_FOO
  type: decision
  do:
    - "Evaluate the value of [foo]."
    - "[foo_try] = [foo_try] + 1"
  route:
    - "IF [foo_try] >= <MAX_RETRY_ATTEMPTS> -> GO_TO: FLOW_EXIT"
    - "IF [foo] IS NULL -> GO_TO: FLOW_ASK_FOO"
    - "IF [foo] == 'yes' -> GO_TO: FLOW_NEXT"
    - "GO_TO: FLOW_ASK_FOO"
```

### E. `message`

Doesn't need a retry policy if it captures nothing.

Should be used for:

- farewells,
- confirmations,
- narrative transitions,
- informational responses.

### F. `action`

Reserved for a mandatory, unconditional tool call — the node's only valid output is the tool call declared in `EXECUTE`. See "Guardrails for tool-driven nodes" below for how to keep an `action` node from being misused past its intended purpose.

### G. `subflow_change`

Should be limited to the context transition itself.

Should not be mixed with captures or validations. Its `do` block is a good place to state explicitly which reference document/section must be loaded before the target state executes — that instruction is otherwise easy to lose.

Example:

```yaml
- state_id: FLOW_TO_OTHER
  type: subflow_change
  do:
    - "Load the reference document for the OTHER_SUBFLOW subflow before continuing."
  route:
    - "GO_TO: OTHER_SUBFLOW__ENTRY"
```

---

## Conditional-skip pattern

Not every question needs to be asked if the answer is already known. A `decision` node can sit before a `question` and branch directly on a runtime input variable (or an already-captured slot) to skip straight past the question when the value is already available.

```yaml
- state_id: FLOW_HAS_NAME
  type: decision
  do:
    - "Evaluate whether {{contact_name}} is available and not NULL."
  route:
    - "IF {{contact_name}} IS NOT NULL -> GO_TO: FLOW_NEXT"
  fallback:
    - "GO_TO: FLOW_ASK_NAME"
```

This keeps the "ask only what you don't already know" rule structural rather than something the model has to infer on its own from prose instructions.

---

## Multi-branch decisions

A decision isn't limited to a binary valid/invalid split. When a captured value has more than two meaningful outcomes, route each one explicitly rather than collapsing them into a generic fallback:

```yaml
- state_id: FLOW_DECIDE_STATUS
  type: decision
  do:
    - "Evaluate the value of [status]."
    - "[status_try] = [status_try] + 1"
  route:
    - "IF [status_try] >= <MAX_RETRY_ATTEMPTS> -> GO_TO: FLOW_EXIT"
    - "IF [status] IS NULL -> GO_TO: FLOW_ASK_STATUS"
    - "IF [status] == 'confirmed' -> GO_TO: FLOW_CONFIRMED"
    - "IF [status] == 'declined' -> GO_TO: FLOW_DECLINED"
    - "IF [status] == 'wrong_target' -> GO_TO: FLOW_WRONG_TARGET"
  fallback:
    - "GO_TO: FLOW_ASK_STATUS"
```

Each distinct outcome gets its own named exit. Resist the temptation to merge two semantically different outcomes (e.g. "declined" and "wrong target") into the same route just because they currently lead to similar-looking next steps — a later change to one of them shouldn't require re-deriving which values it covers.

---

## Closing pattern: farewell message + silent terminal

When a flow needs to say goodbye *and* run a closing action (ending a call, closing a session), split that into two nodes instead of one:

1. a `message` node that delivers the farewell line and routes onward,
2. a `terminal` node — with no `say` — whose only job is `execute` on the closing tool and `final: "yes"`.

```yaml
- state_id: FLOW_BYE
  type: message
  say:
    - "Thanks for your time, goodbye."
  wait: "no"
  route:
    - "GO_TO: FLOW_END"

- state_id: FLOW_END
  type: terminal
  say: []
  wait: "no"
  execute: end_session
  route: []
  final: "yes"
```

Keeping the spoken farewell and the tool call in separate nodes avoids ambiguity about ordering (say first, then close) and keeps the terminal node's contract simple: a terminal node that executes something never also has to carry conversational text.

---

## Guardrails for tool-driven nodes

An `action` node that fetches data from a tool (availability, pricing, records) is a place where a model can be tempted to fill gaps with invented content if the tool result is thin, delayed, or partially missing. Two things keep that from happening:

1. **The `action` node's `do` block states the constraint explicitly and negatively** — not just what to do, but what must never happen without a real tool result:

```yaml
- state_id: FLOW_FETCH
  type: action
  do:
    - "TOOL CALL ONLY: call fetch_options now."
    - "Do not say anything before the tool call."
    - "If the tool does not run, stay in this state — do not offer any option."
  capture:
    - slot: options
      type_expr: "list"
  execute: fetch_options
  route:
    - "IF fetch_options.has_results == true AND [options] IS NOT NULL -> GO_TO: FLOW_PRESENT"
  fallback:
    - "GO_TO: FLOW_NO_RESULTS"
```

2. **`flow_rules` restates the same constraint at the subflow level**, so it applies across every node that touches the captured result, not just the one that fetched it:

```yaml
flow_rules:
  - "FLOW_FETCH is the only authorized point to obtain options. Any request to refresh, change, or see more options routes back through FLOW_FETCH."
  - "The output of fetch_options is the only authorized source of options. Never present, accept, or confirm an option that isn't literally present in [options]."
```

The `action` node's `do` protects that one state; the `flow_rules` entry protects every state downstream that talks about the captured result. Use both together whenever a tool result feeds directly into what the agent tells the user — this is the difference between a flow that can only repeat what a tool actually returned and one that can quietly hallucinate a plausible-sounding answer when the tool is slow or empty.

---

## Retry/repeat handling outside the question-decision pair

The same retry-counter idiom used inside a `decision` node — evaluate, increment, cut off at a maximum — applies just as well to a global handler that reacts to a repeated or missing user utterance, not only to a captured value. A generic pattern:

```yaml
handlers:
  - handler_id: HNDLR_REPEAT
    type: message
    trigger:
      - "can you repeat that"
      - "I didn't hear you"
    say:
      - "Sure, let me repeat that."
    do:
      - "[repeat_count] = [repeat_count] + 1"
    route:
      - "IF [repeat_count] < <MAX_REPEAT_ATTEMPTS> -> GO_TO: [current_state]"
      - "IF [repeat_count] >= <MAX_REPEAT_ATTEMPTS> -> GO_TO: FLOW_NO_INPUT_EXIT"

  - handler_id: HNDLR_RESET_REPEAT
    type: message
    trigger:
      - "__ANY_INPUT__"
    say:
      - "."
    do:
      - "[repeat_count] = 0"
    route:
      - "GO_TO: [current_state]"
```

`[current_state]` lets the handler re-enter wherever the conversation actually was, and a dedicated reset handler (triggered on any normal input) keeps the counter from accumulating across unrelated turns. This is the same three-part shape as the question/decision pattern — evaluate, increment, cut off — just applied at the handler level instead of the state level.

A caution specific to handlers: keep global handler routes targeting namespaced subflow states or subflow-local slots to a minimum. A handler that reads `[subflow_prefix__some_slot]` or routes into `SUBFLOW__SOME_STATE` is coupled to that subflow's internal structure even though handlers are meant to be flow-agnostic — prefer routing to a shared, subflow-independent state when the behavior is genuinely global, and reserve subflow-aware handlers for cases where that coupling is actually intentional.

---

## Full compact-pattern flow

### Classic form, less efficient

```text
question -> registration -> decision -> question -> registration -> decision
```

### Optimized form

```text
question -> decision -> question -> decision
```

Or, in flows with pre-configuration:

```text
start -> init_retry_counts -> question -> decision -> question -> decision
```

This second form reduces nodes without losing the semantics of the process.

---

## Rules for deciding whether a question needs a retry policy

Not every question needs the same treatment, but the practical rule is:

### Should have a retry policy when:

- the data affects eligibility,
- the data drives a critical branch,
- the answer could come back empty, ambiguous, or mis-parsed,
- the data is mandatory to continue,
- the answer can fail due to NLU or ASR errors.

### Can skip a retry policy when:

- it's an informational message,
- it's a farewell,
- it's a subflow transition,
- it's a question with no structural impact,
- the answer isn't used to validate eligibility or routing.

### Conservative default

If a question requires automatic interpretation of the user's answer, it should have:

- a captured slot,
- a retry counter,
- a decision with a cutoff at the maximum number of attempts.

---

## Naming recommendations

### Captured slots

Use clear, consistent, `snake_case` names.

Examples:

- `eligibility_confirmed`
- `applicant_age`
- `identity_confirmed`
- `callback_time_slot`

### Counters

Follow the pattern:

```text
<slot_name>_try
```

Examples:

- `eligibility_confirmed_try`
- `identity_try`
- `callback_day_try`

Use the short `_try` suffix, not a longer form like `_retry_count`. A counter's name is repeated constantly — in `do`, in every `route` condition that checks it, in the increment line — and a longer suffix means paying that extra length every single time it's referenced, across every retry-guarded question in the flow. That adds up in the compiled prompt for no benefit `_try` doesn't already provide.

### States

Naming recommendations:

- `X_ASK_<TOPIC>` for questions,
- `X_DECIDE_<TOPIC>` for validations,
- `X_INIT_RETRY_COUNTS` for initialization,
- `X_EXIT_<REASON>` for exits,
- `X_TO_<DEST>` for subflow changes.

Examples:

- `ID_ASK_CONFIRM_IDENTITY`
- `ID_DECIDE_CONFIRM_IDENTITY`
- `EL_ASK_ELIGIBILITY_REGION`
- `EL_DECIDE_ELIGIBILITY_REGION`
- `CB_ASK_DAY`
- `CB_DECIDE_DAY`

---

## Important rule about `do`

The `do` block should be operative, not narrative.

### Good

```yaml
do:
  - "Evaluate the value of [foo]."
  - "[foo_try] = [foo_try] + 1"
```

### Less ideal

```yaml
do:
  - "Evaluate the value of [foo]."
  - "If [foo] is invalid or couldn't be parsed, increment [foo_try]."
  - "Ask the user again."
```

### Why

The "ask again" instruction is already implicit in `route`.

Repeating it in `do` only grows the compiled prompt without adding any real control.

---

## Recommended retry policy

### Basic rule

Every time the decision runs, its counter goes up by 1.

This simplifies the semantics:

- attempt 1 = first validation pass,
- attempt 2 = second pass,
- attempt 3 = third pass,
- once the maximum is exceeded, route to the defined exit.

### Practical consequence

This avoids a separate node that exists only to increment the counter.

### Caution

If a flow uses this pattern, the retry route must stay consistent with the fact that the counter increases inside the decision.

That means:

- don't assume the counter only goes up when the value is invalid,
- don't duplicate the increment across two different nodes for the same attempt.

---

## How to design a new question with this pattern

### Step 1: define the slot and its counter

If the question can fail or needs validation, create:

- a captured slot,
- a retry counter.

Example:

- `employment_status`
- `employment_status_try`

### Step 2: create the `question` node

Should:

- ask exactly one thing,
- capture the slot,
- persist the value in `store`.

### Step 3: create the `decision` node

Should:

- evaluate the value,
- increment the retry counter,
- apply the maximum,
- define the valid route,
- define the null route,
- define the fallback route.

### Step 4: avoid `registration` unless it adds real value

Only use `registration` when a genuinely separate, semantically distinct action is needed, for example:

- bulk initialization of variables at the start of a flow,
- assigning a derived value that isn't part of the capture,
- integration with a tool or an isolated calculation that shouldn't be mixed in.

---

## When it's still worth keeping a `registration` node

Even though the pattern aims to reduce nodes, that doesn't mean eliminating every `registration`.

Keep one when:

1. there's a global initialization of multiple counters (or slots that need a known starting value),
2. a derived value is being assigned that doesn't come from the user,
3. context needs to be prepared before a transition,
4. the operation has a semantics genuinely different from asking or deciding.

Clear example:

- initializing the counters for an entire flow at the start.

Doubtful example:

- creating a `registration` node just to copy `[slot] = [slot]` and bump a retry counter.

That second case is exactly what this pattern recommends eliminating.

---

## Fallback recommendation

`fallback` should be coherent with the main `route`.

### Good practice

- if the valid route leads to node X,
- the fallback shouldn't invent a radically different route unless that's intentional.

### What the fallback should do

- repeat the question,
- send to the max-attempts exit,
- or follow an already-defined safe policy.

### What the fallback should not do

- duplicate long explanatory text,
- contradict the main logic,
- jump to a node incompatible with the state of the data captured so far.

---

## Minimal full-pattern example

```yaml
states:
  - state_id: FLOW_START
    type: start
    route:
      - "GO_TO: FLOW_INIT_RETRY_COUNTS"

  - state_id: FLOW_INIT_RETRY_COUNTS
    type: registration
    do:
      - "[foo_try] = 0"
    route:
      - "GO_TO: FLOW_ASK_FOO"

  - state_id: FLOW_ASK_FOO
    type: question
    say:
      - "What's your answer?"
    wait: "yes"
    capture:
      - slot: foo
        type_expr: "Literal[yes, no]"
    store:
      - "[foo] = [foo]"
    route:
      - "GO_TO: FLOW_DECIDE_FOO"

  - state_id: FLOW_DECIDE_FOO
    type: decision
    do:
      - "Evaluate the value of [foo]."
      - "[foo_try] = [foo_try] + 1"
    route:
      - "IF [foo_try] >= <MAX_RETRY_ATTEMPTS> -> GO_TO: FLOW_END"
      - "IF [foo] IS NULL -> GO_TO: FLOW_ASK_FOO"
      - "IF [foo] == 'yes' -> GO_TO: FLOW_NEXT"
      - "IF [foo] == 'no' -> GO_TO: FLOW_END"
```

---

## Checklist to replicate the pattern

Before closing a flow, verify:

- [ ] Every important question captures its data directly.
- [ ] Every captured value is persisted in the `store` of the `question` node.
- [ ] There's no intermediate `registration` node that only copies the same value.
- [ ] Every validation has its associated `decision`.
- [ ] Every `decision` increments its retry counter.
- [ ] Every retry counter has a limit via `<MAX_RETRY_ATTEMPTS>`.
- [ ] The max-attempts route leads to a defined exit.
- [ ] The `NULL` route asks again.
- [ ] The valid route advances to the next state.
- [ ] `do` doesn't repeat unnecessary text.
- [ ] `fallback` is coherent with `route`.
- [ ] A closing node that runs `execute` doesn't also carry a farewell `say` — split them.
- [ ] Any `action` node that fetches data has an explicit "don't invent, don't proceed without a real result" instruction in `do`, backed by a matching `flow_rules` entry if the result feeds multiple downstream states.
- [ ] User-facing messages remain unchanged unless there's a real reason to alter them.

---

## Common mistakes to avoid

### 1. Creating a `registration` node that just repeats the `question`'s assignment

This adds noise without adding value.

### 2. Incrementing the counter in two different places

The counter should have one clear increment point per validation cycle.

### 3. Leaving a question with no max-attempts exit

If the data matters, there shouldn't be an infinite wait.

### 4. Making `do` say the same thing as `route`

`do` and `route` shouldn't semantically duplicate each other.

### 5. Leaving `fallback` and `route` contradicting each other

This complicates reading the flow and can produce unclear behavior.

### 6. Letting a tool-fetching `action` node imply a result without one

If a node captures a tool result that later gets spoken to the user, its `do` (and, if the result is reused, a `flow_rules` entry) must forbid presenting anything the tool didn't literally return.

---

## Recommended design criterion

If an interaction can be expressed with a single question and a single decision, that's usually the preferred form.

Only split into more nodes when there's a real reason:

- context initialization,
- a derived calculation,
- integration with a tool,
- a semantically distinct transition.

Otherwise, compact it.

---

## Executive summary

The cleanest way to model these flows is:

- `start` to enter,
- `registration` only to initialize or prepare real context,
- `question` to capture and store,
- `decision` to validate, increment retries, and decide,
- `message` to communicate,
- `action` for a mandatory, unconditional tool call,
- `subflow_change` to change subflow,
- `terminal` to close.

The single most important rule:

> If a node only exists to copy a captured value and bump a retry counter, it can usually be removed and absorbed into the `question` and the `decision`.

That's the pattern that reduces nodes while keeping the functionality intact.
