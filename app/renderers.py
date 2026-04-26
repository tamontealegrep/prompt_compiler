"""Rendering of System Prompt and Reference Asset artifacts.

Two output families share this module:

1. **System Prompt** — produced by ``render_prompt(classified, template,
   channel_profile)``. The Jinja2 template under ``templates/`` is filled
   with the named blocks built by ``build_render_context``. Channel-driven
   policy rendering iterates ``channel_profile.policy_sections`` so adding
   a section to a channel never requires touching the renderer.
2. **Reference Asset** — produced by
   ``render_reference_asset_markdown(classified)`` (RAG-friendly MD) and
   ``render_reference_asset_json(classified)`` (stable dict). Both consume
   the slices that the classifier extracted (FAQs, tool contracts,
   context).

The renderer is the only consumer of ``say_verbatim``: when a SAY block
is marked verbatim it is annotated ``[verbatim]`` in the output;
otherwise ``[flexible]``. The flag is rendered next to the block label so
the LLM cannot miss it.

Low-level formatters (``_quote``, ``_render_plain_list``,
``_render_labeled_block``, ``_render_capture_block`` …) are private —
they keep the indentation conventions consistent across every block.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from app.classifier import ClassifiedSpec
from app.schemas import (
    AgentSpec,
    ChannelProfile,
    ContextFile,
    FAQModel,
    HandlerModel,
    StateModel,
    ToolContract,
)


# ---------------------------------------------------------------------------
# Low-level formatting helpers (private)
# ---------------------------------------------------------------------------


def _quote(text: str) -> str:
    """Wrap ``text`` in double quotes, escaping any embedded ones."""
    return '"' + text.replace('"', '\\"') + '"'


def _render_plain_list(lines: list[str], indent: int = 0) -> str:
    """Render ``lines`` as a Markdown bullet list."""
    pad = " " * indent
    return "\n".join(f"{pad}- {line}" for line in lines)


def _render_labeled_block(
    label: str,
    lines: list[str],
    *,
    indent: int = 0,
    quote_items: bool = False,
) -> str:
    """Render ``- LABEL:`` followed by indented bullet items.

    Returns an empty string when ``lines`` is empty so callers can chain
    via ``_join_blocks`` without conditional logic.
    """
    if not lines:
        return ""
    pad = " " * indent
    rendered = [f"{pad}- `{label}`:"]
    for line in lines:
        value = _quote(line) if quote_items else line
        rendered.append(f"{pad}  - {value}")
    return "\n".join(rendered)


def _render_say_block(
    lines: list[str], *, verbatim_label: str, indent: int = 0
) -> str:
    """Render the SAY block with the ``[verbatim]`` / ``[flexible]`` annotation.

    SAY items are always quoted so the LLM treats them as literal user-
    facing intent rather than as instructions to itself.
    """
    if not lines:
        return ""
    pad = " " * indent
    rendered = [f"{pad}- `SAY` {verbatim_label}:"]
    for line in lines:
        rendered.append(f"{pad}  - {_quote(line)}")
    return "\n".join(rendered)


def _render_scalar(label: str, value: str, *, indent: int = 0) -> str:
    """Render a single ``- LABEL: \\`value\\``` line."""
    pad = " " * indent
    return f"{pad}- `{label}`: `{value}`"


def _render_capture_block(capture: list, *, indent: int = 0) -> str:
    """Render a CAPTURE block listing slot/type pairs."""
    if not capture:
        return ""
    pad = " " * indent
    rendered = [f"{pad}- `CAPTURE`:"]
    for item in capture:
        rendered.append(f"{pad}  - `[{item.slot}]`: `{item.type_expr}`")
    return "\n".join(rendered)


def _render_execute_block(execute: str | None, *, indent: int = 0) -> str:
    """Render an EXECUTE tool reference."""
    if not execute:
        return ""
    pad = " " * indent
    return "\n".join(
        [
            f"{pad}- `EXECUTE`:",
            f"{pad}  - `{execute}`",
        ]
    )


def _join_blocks(parts: list[str]) -> str:
    """Join non-empty blocks with newlines, dropping empties."""
    return "\n".join(part for part in parts if part)


def _verbatim_label(say_verbatim: bool) -> str:
    """Return the SAY-block annotation for the renderer."""
    return "[verbatim]" if say_verbatim else "[flexible]"


# ---------------------------------------------------------------------------
# System Prompt renderers
# ---------------------------------------------------------------------------


def render_system_constants(spec: AgentSpec) -> str:
    """Render the ``# SYSTEM CONSTANTS`` table block."""
    lines = [
        "# SYSTEM CONSTANTS",
        "The following constants define the core parameters of the agent's "
        "operation. These values are fixed and must be used exactly as defined.",
        "",
        "| Constant | Description | Value |",
        "| :--- | :--- | :--- |",
    ]
    for item in spec.constants:
        lines.append(f"| <{item.name}> | {item.description} | {item.value} |")
    return "\n".join(lines)


def render_input_variables(spec: AgentSpec) -> str:
    """Render the ``# INPUT VARIABLES`` block."""
    lines = ["# INPUT VARIABLES"]
    for item in spec.input_variables:
        lines.append(f"- `{{{{{item.name}}}}}`: {item.description}")
        if item.allowed_values:
            for value in item.allowed_values:
                lines.append(f'  * "{value}"')
        lines.append("")
    return "\n".join(lines).rstrip()


def render_tools(tool_names: list[str]) -> str:
    """Render the tool **names** only — full contracts go to the Reference Asset."""
    lines = ["# AGENT TOOLS"]
    for tool in tool_names:
        lines.append(f"- `{tool}`")
    return "\n".join(lines)


def render_identity(spec: AgentSpec) -> str:
    """Render the ``# IDENTITY`` bullet list."""
    lines = ["# IDENTITY"]
    lines.extend(f"- {line}" for line in spec.identity)
    return "\n".join(lines)


def render_objectives(spec: AgentSpec) -> str:
    """Render ``# OBJECTIVES`` with primary/secondary/success-alternative subsections."""
    lines = [
        "# OBJECTIVES",
        "## PRIMARY_OBJECTIVE",
        _render_plain_list(spec.objectives.primary_objective),
        "",
        "## SECONDARY_OBJECTIVES",
        _render_plain_list(spec.objectives.secondary_objectives),
        "",
        "## SUCCESS_ALTERNATIVES",
        _render_plain_list(spec.objectives.success_alternatives),
    ]
    return "\n".join(lines)


def render_global_policies(
    spec: AgentSpec, channel_profile: ChannelProfile
) -> str:
    """Render policy sections in the order declared by the channel profile.

    Empty sections are skipped silently — required-section emptiness has
    already been enforced by the loaders' ``_merge_policy_fragments``.
    """
    lines = ["# GLOBAL OPERATING POLICIES"]
    for section_def in channel_profile.policy_sections:
        section_lines = spec.policies.get_section(section_def.name)
        if not section_lines:
            continue
        lines.extend(
            [
                "",
                f"## {section_def.label}",
                _render_plain_list(section_lines),
            ]
        )
    return "\n".join(lines)


def render_flow_entry(spec: AgentSpec) -> str:
    """Render the ``START_AT`` reference."""
    return f"- `START_AT: {spec.manifest.start_at}`"


def render_flow_rules(spec: AgentSpec) -> str:
    """Render the ``FLOW_RULES`` bullet list."""
    return _render_plain_list(spec.flow_rules)


def render_faq_policy(spec: AgentSpec) -> str:
    """Render the ``FAQ_POLICY`` bullet list (the cross-cutting routing policy)."""
    return _render_plain_list(spec.faq_policy)


def render_handler(handler: HandlerModel) -> str:
    """Render a single handler block including ``[verbatim]`` / ``[flexible]``."""
    parts = [
        f"### HANDLER {handler.handler_id}",
        _render_scalar("HANDLER_ID", handler.handler_id),
        _render_scalar("TYPE", handler.type),
        _render_labeled_block("GOAL", handler.goal),
        _render_labeled_block("TRIGGER", handler.trigger),
    ]

    if handler.say:
        parts.append(
            _render_say_block(
                handler.say, verbatim_label=_verbatim_label(handler.say_verbatim)
            )
        )

    if handler.wait is not None:
        parts.append(_render_scalar("WAIT", handler.wait))

    parts.extend(
        [
            _render_capture_block(handler.capture),
            _render_labeled_block("STORE", handler.store),
            _render_labeled_block("ROUTE", handler.route),
            _render_execute_block(handler.execute),
            _render_labeled_block("FALLBACK", handler.fallback),
        ]
    )

    if handler.final is not None:
        parts.append(_render_scalar("FINAL", handler.final))

    return _join_blocks(parts)


def render_handlers(spec: AgentSpec) -> str:
    """Render every handler separated by blank lines."""
    return "\n\n".join(render_handler(h) for h in spec.handlers)


def render_state(state: StateModel) -> str:
    """Render a single state including ``[verbatim]`` / ``[flexible]``."""
    parts = [
        f"### STATE {state.state_id}",
        _render_scalar("STATE_ID", state.state_id),
        _render_scalar("TYPE", state.type),
        _render_labeled_block("GOAL", state.goal),
        _render_labeled_block("DO", state.do),
    ]

    if state.say:
        parts.append(
            _render_say_block(
                state.say, verbatim_label=_verbatim_label(state.say_verbatim)
            )
        )

    if state.wait is not None:
        parts.append(_render_scalar("WAIT", state.wait))

    parts.extend(
        [
            _render_capture_block(state.capture),
            _render_labeled_block("STORE", state.store),
            _render_execute_block(state.execute),
            _render_labeled_block("ROUTE", state.route),
        ]
    )

    if state.faq_resume_to is not None:
        parts.append(_render_scalar("FAQ_RESUME_TO", state.faq_resume_to))

    parts.append(_render_labeled_block("FALLBACK", state.fallback))

    if state.final is not None:
        parts.append(_render_scalar("FINAL", state.final))

    return _join_blocks(parts)


def render_states(spec: AgentSpec) -> str:
    """Render every main state separated by blank lines."""
    return "\n\n".join(render_state(s) for s in spec.states)


def render_terminal_states(spec: AgentSpec) -> str:
    """Render every terminal state separated by blank lines."""
    return "\n\n".join(render_state(s) for s in spec.terminal_states)


# ---------------------------------------------------------------------------
# Subflow-aware renderers (lightweight system prompt + per-subflow docs)
# ---------------------------------------------------------------------------


def _get_subflow_namespaces(spec: AgentSpec) -> list[str]:
    """Return sorted unique subflow namespaces extracted from state IDs."""
    namespaces: set[str] = set()
    for state in list(spec.states) + list(spec.terminal_states):
        if "__" in state.state_id:
            namespaces.add(state.state_id.split("__")[0])
    return sorted(namespaces)


def _subflow_entry_state(spec: AgentSpec, namespace: str) -> StateModel | None:
    """Return the entry state for a subflow.

    Prefers a ``start`` type node; falls back to the first state in
    declaration order that belongs to the namespace.
    """
    ns_prefix = namespace + "__"
    candidates = [s for s in spec.states if s.state_id.startswith(ns_prefix)]
    for s in candidates:
        if s.type == "start":
            return s
    return candidates[0] if candidates else None


def render_subflow_index(spec: AgentSpec) -> str:
    """Render the AVAILABLE_SUBFLOWS table listing all subflows and their entry points."""
    namespaces = _get_subflow_namespaces(spec)
    if not namespaces:
        return ""
    lines = [
        "## AVAILABLE_SUBFLOWS",
        "",
        "| Subflow | Entry State | Description |",
        "| :--- | :--- | :--- |",
    ]
    for ns in namespaces:
        entry = _subflow_entry_state(spec, ns)
        entry_id = entry.state_id if entry else f"{ns}__[unknown]"
        description = entry.goal[0] if (entry and entry.goal) else "—"
        lines.append(f"| `{ns}` | `{entry_id}` | {description} |")
    return "\n".join(lines)


def render_root_states(spec: AgentSpec) -> str:
    """Render only root-level states (no ``__`` in ID, i.e. not subflow-owned)."""
    root = [s for s in spec.states if "__" not in s.state_id]
    if not root:
        return ""
    return "\n\n".join(render_state(s) for s in root)


def render_root_terminal_states(spec: AgentSpec) -> str:
    """Render only root-level terminal states (no ``__`` in ID)."""
    root = [s for s in spec.terminal_states if "__" not in s.state_id]
    if not root:
        return ""
    return "\n\n".join(render_state(s) for s in root)


def render_subflow_document(spec: AgentSpec, namespace: str) -> str:
    """Render a self-contained reference document for one subflow.

    The document lists the entry point, any ``subflow_change`` exit nodes,
    then all states and terminal states that belong to the namespace.
    """
    ns_prefix = namespace + "__"
    subflow_states = [s for s in spec.states if s.state_id.startswith(ns_prefix)]
    subflow_terminals = [
        s for s in spec.terminal_states if s.state_id.startswith(ns_prefix)
    ]

    entry = _subflow_entry_state(spec, namespace)
    entry_id = entry.state_id if entry else "—"

    lines: list[str] = [
        f"# SUBFLOW: {namespace}",
        "",
        f"**Entry state:** `{entry_id}`",
        "",
    ]

    exits = [s for s in subflow_states if s.type == "subflow_change"]
    if exits:
        lines.append("**Subflow exits (subflow_change nodes):**")
        for ex in exits:
            targets = [
                t
                for rule in (ex.route or []) + (ex.fallback or [])
                for t in _extract_goto_targets(rule)
            ]
            target_str = ", ".join(f"`{t}`" for t in targets) if targets else "—"
            lines.append(f"- `{ex.state_id}` → {target_str}")
        lines.append("")

    if subflow_states:
        lines.append("## STATES")
        lines.append("")
        lines.append("\n\n".join(render_state(s) for s in subflow_states))

    if subflow_terminals:
        lines.append("")
        lines.append("## TERMINAL_STATES")
        lines.append("")
        lines.append("\n\n".join(render_state(s) for s in subflow_terminals))

    return "\n".join(lines).rstrip() + "\n"


def _extract_goto_targets(rule: str) -> list[str]:
    """Extract GO_TO target state IDs from a route/fallback rule string."""
    import re
    return re.findall(r"\bGO_TO:\s*([A-Z][A-Z0-9_]*)", rule)


def render_all_subflow_documents(spec: AgentSpec) -> dict[str, str]:
    """Return a mapping of namespace → rendered subflow document."""
    return {
        ns: render_subflow_document(spec, ns)
        for ns in _get_subflow_namespaces(spec)
    }


# ---------------------------------------------------------------------------
# Reference Asset renderers
# ---------------------------------------------------------------------------


def _render_reference_context(context: ContextFile) -> str:
    """Render ``context.*`` as Markdown for RAG ingestion."""
    lines = ["## Company Context", ""]
    lines.extend(f"- {line}" for line in context.company_context)

    lines.extend(["", "## Approved Services", ""])
    lines.extend(f"- {line}" for line in context.approved_services)

    lines.extend(["", "## Services Library", ""])
    for item in context.summary_services_library:
        lines.extend(
            [
                f"### {item.key}",
                f"**Procedure:** `{item.procedure}`",
                "",
                item.text,
                "",
            ]
        )

    lines.extend(["## Approved Process", "", context.approved_process_intro, ""])
    for idx, step in enumerate(context.approved_process_steps, start=1):
        lines.append(f"{idx}. **{step.title}** — {step.text}")

    lines.extend(["", "## Support and Trust", ""])
    lines.extend(f"- {line}" for line in context.support_and_trust)

    return "\n".join(lines).rstrip()


def _render_reference_tool_contracts(contracts: list[ToolContract]) -> str:
    """Render every tool contract as Markdown."""
    if not contracts:
        return ""

    lines = ["## Tool Contracts", ""]
    for contract in contracts:
        lines.extend(
            [
                f"### `{contract.name}`",
                f"**Description:** {contract.description}",
                "",
            ]
        )

        if contract.inputs:
            lines.append("**Inputs:**")
            for fld in contract.inputs:
                req = "required" if fld.required else "optional"
                lines.append(f"- `{fld.name}` ({req}): {fld.description}")
            lines.append("")
        else:
            lines.extend(["**Inputs:** none", ""])

        if contract.outputs:
            lines.append("**Outputs:**")
            for fld in contract.outputs:
                req = "required" if fld.required else "optional"
                lines.append(f"- `{fld.name}` ({req}): {fld.description}")
            lines.append("")
        else:
            lines.extend(["**Outputs:** none", ""])

        if contract.notes:
            lines.append("**Notes:**")
            for note in contract.notes:
                lines.append(f"- {note}")
            lines.append("")

    return "\n".join(lines).rstrip()


def _render_reference_faqs(faqs: list[FAQModel]) -> str:
    """Render every FAQ as Markdown."""
    if not faqs:
        return ""

    lines = ["## FAQs", ""]
    for faq in faqs:
        lines.extend(
            [
                f"### FAQ: {faq.faq_id}",
                "**Match phrases:**",
            ]
        )
        for phrase in faq.match:
            lines.append(f'- "{phrase}"')
        lines.extend(["", "**Answer:**"])
        for line in faq.answer:
            lines.append(f'- "{line}"')
        lines.append("")

    return "\n".join(lines).rstrip()


def render_reference_asset_markdown(classified: ClassifiedSpec) -> str:
    """Render the Reference Asset as structured Markdown for RAG.

    Top-level ``H2`` boundaries are intentional: most chunkers split on
    ``##`` headings, so each section becomes its own retrievable chunk.
    Sections are separated by ``---`` rules to disambiguate boundaries
    further.
    """
    header = f"# Reference Asset — {classified.spec.manifest.agent_id}"
    sections = [
        header,
        _render_reference_context(classified.reference_context),
        _render_reference_tool_contracts(classified.reference_tool_contracts),
        _render_reference_faqs(classified.reference_faqs),
    ]
    rendered = "\n\n---\n\n".join(s for s in sections if s)
    return rendered.rstrip() + "\n"


def render_reference_asset_json(classified: ClassifiedSpec) -> dict[str, Any]:
    """Render the Reference Asset as a JSON-serializable dict.

    Keys are stable so a downstream RAG ingester can rely on them. Lists
    are materialized to plain Python lists (no Pydantic objects leak).
    """
    ctx = classified.reference_context
    return {
        "agent_id": classified.spec.manifest.agent_id,
        "context": {
            "company_context": list(ctx.company_context),
            "approved_services": list(ctx.approved_services),
            "services_library": [
                {
                    "key": item.key,
                    "procedure": item.procedure,
                    "text": item.text,
                }
                for item in ctx.summary_services_library
            ],
            "approved_process": {
                "intro": ctx.approved_process_intro,
                "steps": [
                    {"title": s.title, "text": s.text}
                    for s in ctx.approved_process_steps
                ],
            },
            "support_and_trust": list(ctx.support_and_trust),
        },
        "tool_contracts": [
            {
                "name": c.name,
                "description": c.description,
                "inputs": [
                    {
                        "name": f.name,
                        "required": f.required,
                        "description": f.description,
                    }
                    for f in c.inputs
                ],
                "outputs": [
                    {
                        "name": f.name,
                        "required": f.required,
                        "description": f.description,
                    }
                    for f in c.outputs
                ],
                "notes": list(c.notes),
            }
            for c in classified.reference_tool_contracts
        ],
        "faqs": [
            {
                "faq_id": faq.faq_id,
                "type": faq.type,
                "match": list(faq.match),
                "answer": list(faq.answer),
            }
            for faq in classified.reference_faqs
        ],
    }


# ---------------------------------------------------------------------------
# Jinja2 template assembly
# ---------------------------------------------------------------------------


def build_render_context(
    classified: ClassifiedSpec, channel_profile: ChannelProfile
) -> dict[str, str]:
    """Assemble the dict of named blocks consumed by the Jinja2 template.

    ``states_block`` and ``terminal_states_block`` contain only root-level
    states (no ``__`` in the ID). Subflow states are rendered separately
    into per-subflow reference documents by ``render_all_subflow_documents``.
    """
    spec = classified.spec
    return {
        "system_constants_block": render_system_constants(spec),
        "input_variables_block": render_input_variables(spec),
        "agent_tools_block": render_tools(classified.prompt_tool_names),
        "identity_block": render_identity(spec),
        "objectives_block": render_objectives(spec),
        "global_operating_policies_block": render_global_policies(
            spec, channel_profile
        ),
        "flow_entry_block": render_flow_entry(spec),
        "flow_rules_block": render_flow_rules(spec),
        "handlers_block": render_handlers(spec),
        "faq_policy_block": render_faq_policy(spec),
        "subflow_index_block": render_subflow_index(spec),
        "states_block": render_root_states(spec),
        "terminal_states_block": render_root_terminal_states(spec),
    }


def render_prompt(
    classified: ClassifiedSpec,
    template_path: Path,
    channel_profile: ChannelProfile,
) -> str:
    """Render the System Prompt using the Jinja2 template at ``template_path``.

    The Jinja environment uses ``[[`` / ``]]`` as variable delimiters
    instead of the default ``{{`` / ``}}`` to avoid clashing with the
    DSL's runtime-variable notation, which uses double curly braces in
    user-facing text.
    """
    if not template_path.exists():
        raise FileNotFoundError(f"No existe el template: {template_path}")

    env = Environment(
        loader=FileSystemLoader(str(template_path.parent)),
        autoescape=False,
        trim_blocks=False,
        lstrip_blocks=False,
        variable_start_string="[[",
        variable_end_string="]]",
    )

    template = env.get_template(template_path.name)
    context = build_render_context(classified, channel_profile)
    return template.render(**context).strip() + "\n"