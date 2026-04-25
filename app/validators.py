"""Graph and compliance validation for ``AgentSpec``.

This module hosts every static-analysis check the compiler performs after
the loaders assemble the spec. There are two families of checks:

1. **Graph integrity (11 validators)** — preserved from the legacy
   compiler. Cover duplicate ids, dangling ``GO_TO`` targets, tool/
   contract consistency, placeholder declarations, FAQ collisions,
   reachability, cycle detection, redundant ``ROUTE``/``FALLBACK``, and
   question self-loops without a retry counter. Build a NetworkX graph
   once and reuse it for reachability/cycle analysis.
2. **Compliance (4 checkers)** — new in v2. Triggered when the caller
   passes a ``ComplianceProfile``. Checkers are registered in the
   ``_COMPLIANCE_CHECKERS`` dict; adding a new check means writing one
   function plus appending one entry — the pipeline does not change.

All checks emit issues via ``ValidationReport`` (errors/warnings). The
report is mutable; the public API ``validate_agent_spec`` returns it
populated. Severity of compliance violations is controlled by the
profile YAML (per-rule), not by the code, so a security team can promote
a warning to an error without a code change.

The orphan-state diagnostic ``build_orphan_state_report`` is exposed as a
separate public function because the CLI writes it to its own artifact.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

import networkx as nx

from app.schemas import (
    AgentSpec,
    ComplianceProfile,
    ComplianceRuleDefinition,
    ComplianceSeverity,
)
from app.utils import (
    dynamic_target_slot_name,
    extract_assignment_slots,
    extract_constants,
    extract_goto_targets,
    extract_slots,
    extract_variables,
    is_dynamic_target,
    iter_all_flow_objects,
    iter_state_objects,
    iter_text_fields,
    normalize_phrase,
    terminal_state_ids,
    tool_contract_map,
)


# ---------------------------------------------------------------------------
# Issue and report containers
# ---------------------------------------------------------------------------


@dataclass
class ValidationIssue:
    """A single validation finding."""

    severity: str  # "ERROR" or "WARNING"
    code: str
    location: str
    message: str


@dataclass
class ValidationReport:
    """Accumulated validation findings for one ``AgentSpec``.

    Mutable on purpose — every validator appends to it. The compiler is
    the only consumer that reads from it; CLI integrations should access
    the public properties (``has_errors``, ``has_warnings``,
    ``to_markdown``) instead of poking the lists directly.
    """

    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)

    def add_error(self, code: str, location: str, message: str) -> None:
        """Append an error-level issue."""
        self.errors.append(ValidationIssue("ERROR", code, location, message))

    def add_warning(self, code: str, location: str, message: str) -> None:
        """Append a warning-level issue."""
        self.warnings.append(ValidationIssue("WARNING", code, location, message))

    def has_errors(self) -> bool:
        """Return ``True`` if any error was recorded."""
        return bool(self.errors)

    def has_warnings(self) -> bool:
        """Return ``True`` if any warning was recorded."""
        return bool(self.warnings)

    def to_markdown(self) -> str:
        """Render the report as Markdown — used by the CLI artifact writer."""
        lines = [
            "# Validation Report",
            "",
            f"- Errors: {len(self.errors)}",
            f"- Warnings: {len(self.warnings)}",
            "",
            "## Errors",
        ]
        if not self.errors:
            lines.append("- None")
        else:
            for issue in self.errors:
                lines.append(
                    f"- **[{issue.code}]** `{issue.location}`: {issue.message}"
                )

        lines.extend(["", "## Warnings"])
        if not self.warnings:
            lines.append("- None")
        else:
            for issue in self.warnings:
                lines.append(
                    f"- **[{issue.code}]** `{issue.location}`: {issue.message}"
                )

        lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def validate_agent_spec(
    spec: AgentSpec,
    compliance_profile: ComplianceProfile | None = None,
) -> ValidationReport:
    """Run every graph validator (and compliance checks if a profile is given).

    Order matters because earlier validators provide context that later
    ones rely on (e.g. ``_validate_duplicates`` runs first so cross-scope
    id collisions are reported with their sources). Compliance runs last
    so its messages never collide with structural errors.
    """
    report = ValidationReport()

    _validate_duplicates(spec, report)
    _validate_start_at(spec, report)
    _validate_faq_resume_targets(spec, report)
    _validate_goto_targets(spec, report)
    _validate_tools_and_contracts(spec, report)
    _validate_placeholders_and_memory_slots(spec, report)
    _validate_summary_coverage(spec, report)
    _validate_faq_match_collisions(spec, report)
    _validate_reachability_and_cycles(spec, report)
    _validate_redundant_route_fallback(spec, report)
    _validate_question_self_loops(spec, report)

    if compliance_profile is not None:
        validate_compliance(spec, compliance_profile, report)

    return report


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _sources_for(spec: AgentSpec, object_id: str) -> str:
    """Return the comma-joined provenance of ``object_id`` (or a fallback)."""
    sources = spec.object_sources.get(object_id, [])
    if not sources:
        return "unknown_source"
    return ", ".join(sources)


def _canonical_slot_name(slot_name: str) -> str:
    """Strip the namespace prefix from a slot name (e.g. ``inst__foo`` → ``foo``).

    Used by the tool-contract validator to compare CAPTURE slot names
    against contract output names regardless of the subflow instance.
    """
    if "__" not in slot_name:
        return slot_name
    prefix, suffix = slot_name.split("__", 1)
    if prefix and suffix:
        return suffix
    return slot_name


def _faq_entry_targets(spec: AgentSpec) -> set[str]:
    """Return the set of states reachable as FAQ entry points via ``faq_policy``."""
    all_state_ids = set(spec.all_state_ids)
    result: set[str] = set()
    for line in spec.faq_policy:
        for target in extract_goto_targets(line):
            if not is_dynamic_target(target) and target in all_state_ids:
                result.add(target)
    return result


def _states_with_dynamic_exit_path(
    spec: AgentSpec, state_graph: nx.DiGraph
) -> set[str]:
    """Return the set of states that can reach a state with a dynamic GO_TO."""
    dynamic_exit_states: set[str] = set()
    for state in iter_state_objects(spec):
        for line in state.route + state.fallback:
            targets = extract_goto_targets(line)
            if any(is_dynamic_target(t) for t in targets):
                dynamic_exit_states.add(state.state_id)
                break

    result: set[str] = set()
    for state_id in state_graph.nodes:
        for dynamic_state_id in dynamic_exit_states:
            if dynamic_state_id in state_graph and nx.has_path(
                state_graph, state_id, dynamic_state_id
            ):
                result.add(state_id)
                break
    return result


# ---------------------------------------------------------------------------
# Graph validators (1..11)
# ---------------------------------------------------------------------------


def _validate_duplicates(spec: AgentSpec, report: ValidationReport) -> None:
    """Detect duplicate ids in constants, input vars, tools, contracts, slots and flow objects."""
    constant_counts = Counter(item.name for item in spec.constants)
    for name, count in constant_counts.items():
        if count > 1:
            report.add_error(
                "DUPLICATE_CONSTANT",
                "constants.yaml",
                f"La constante {name!r} está duplicada {count} veces.",
            )

    input_counts = Counter(item.name for item in spec.input_variables)
    for name, count in input_counts.items():
        if count > 1:
            report.add_error(
                "DUPLICATE_INPUT_VARIABLE",
                "input_variables.yaml",
                f"La variable runtime {name!r} está duplicada {count} veces.",
            )

    tool_counts = Counter(spec.tools)
    for name, count in tool_counts.items():
        if count > 1:
            report.add_error(
                "DUPLICATE_TOOL",
                "tools.yaml",
                f"La tool {name!r} está duplicada {count} veces.",
            )

    contract_counts = Counter(c.name for c in spec.tool_contracts)
    for name, count in contract_counts.items():
        if count > 1:
            report.add_error(
                "DUPLICATE_TOOL_CONTRACT",
                "tool_contracts.yaml",
                f"El contrato de tool {name!r} está duplicado {count} veces.",
            )

    slot_counts = Counter(slot.name for slot in spec.memory_slots)
    for name, count in slot_counts.items():
        if count > 1:
            report.add_error(
                "DUPLICATE_MEMORY_SLOT",
                "memory_slots.yaml",
                f"El memory slot {name!r} está duplicado {count} veces.",
            )

    summary_key_counts = Counter(
        item.key for item in spec.context.summary_services_library
    )
    for key, count in summary_key_counts.items():
        if count > 1:
            report.add_error(
                "DUPLICATE_SUMMARY_KEY",
                "context.yaml",
                f"La summary key {key!r} está duplicada {count} veces.",
            )

    summary_proc_counts = Counter(
        item.procedure for item in spec.context.summary_services_library
    )
    for proc, count in summary_proc_counts.items():
        if count > 1:
            report.add_error(
                "DUPLICATE_SUMMARY_PROCEDURE",
                "context.yaml",
                f"Hay {count} summaries para el procedure {proc!r}. "
                f"Debe existir solo una por procedure.",
            )

    usage: dict[str, list[str]] = defaultdict(list)
    for handler in spec.handlers:
        usage[handler.handler_id].append("handlers")
    for state in spec.states:
        usage[state.state_id].append("states")
    for state in spec.terminal_states:
        usage[state.state_id].append("terminal_states")
    for faq in spec.faqs:
        usage[faq.faq_id].append("faqs")

    for obj_id, scopes in usage.items():
        if len(scopes) > 1:
            report.add_error(
                "DUPLICATE_OBJECT_ID",
                "flow",
                f"El ID {obj_id!r} aparece múltiples veces en scopes {scopes}. "
                f"Fuentes: {_sources_for(spec, obj_id)}",
            )


def _validate_start_at(spec: AgentSpec, report: ValidationReport) -> None:
    """Verify ``manifest.start_at`` references an existing main state."""
    main_state_ids = set(spec.main_state_ids)
    if spec.manifest.start_at not in main_state_ids:
        report.add_error(
            "INVALID_START_AT",
            "manifest.start_at",
            f"START_AT apunta a {spec.manifest.start_at!r}, "
            f"pero no existe en los main states agregados.",
        )


def _validate_faq_resume_targets(spec: AgentSpec, report: ValidationReport) -> None:
    """Check ``faq_resume_to`` targets and ``FAQ_POLICY`` entry points."""
    main_state_ids = set(spec.main_state_ids)
    faq_entry_targets = _faq_entry_targets(spec)

    if spec.faq_policy and not faq_entry_targets:
        report.add_error(
            "MISSING_FAQ_ENTRYPOINT",
            "faq_policy",
            "Existe FAQ_POLICY, pero no se encontró ningún GO_TO estático "
            "válido hacia un FAQ entry state existente.",
        )

    faq_resume_used = False
    for state in spec.states + spec.terminal_states:
        if state.faq_resume_to:
            faq_resume_used = True
            if state.faq_resume_to not in main_state_ids:
                report.add_error(
                    "INVALID_FAQ_RESUME_TO",
                    f"state.{state.state_id}.faq_resume_to",
                    f"FAQ_RESUME_TO apunta a {state.faq_resume_to!r}, "
                    f"pero no existe entre los main states.",
                )

    if faq_resume_used and not spec.faq_policy:
        report.add_warning(
            "FAQ_RESUME_WITHOUT_POLICY",
            "flow",
            "Hay estados con FAQ_RESUME_TO, pero FAQ_POLICY está vacío.",
        )


def _validate_target(
    target: str,
    location: str,
    report: ValidationReport,
    known_state_ids: set[str],
    dynamic_slots: set[str],
) -> None:
    """Validate one ``GO_TO`` target — static id or dynamic slot."""
    if is_dynamic_target(target):
        slot_name = dynamic_target_slot_name(target)
        if slot_name not in dynamic_slots:
            report.add_error(
                "INVALID_DYNAMIC_GOTO_SLOT",
                location,
                f"GO_TO dinámico usa el slot {slot_name!r}, "
                f"pero no está permitido en manifest.dynamic_state_slots.",
            )
    else:
        if target not in known_state_ids:
            report.add_error(
                "UNKNOWN_GOTO_TARGET",
                location,
                f"GO_TO apunta a {target!r}, pero ese estado no existe.",
            )


def _validate_goto_targets(spec: AgentSpec, report: ValidationReport) -> None:
    """Validate every ``GO_TO`` reference in routes, fallbacks and faq_policy."""
    known_state_ids = set(spec.all_state_ids)
    dynamic_slots = set(spec.manifest.dynamic_state_slots)

    for category, obj_id, obj in iter_all_flow_objects(spec):
        for field_name, lines in (("route", obj.route), ("fallback", obj.fallback)):
            for idx, line in enumerate(lines, start=1):
                location = f"{category}.{obj_id}.{field_name}[{idx}]"
                if "GO_TO:" in line and not extract_goto_targets(line):
                    report.add_error(
                        "UNPARSEABLE_GOTO",
                        location,
                        "La línea contiene GO_TO:, pero no se pudo extraer un target válido.",
                    )
                for target in extract_goto_targets(line):
                    _validate_target(target, location, report, known_state_ids, dynamic_slots)

    for idx, line in enumerate(spec.faq_policy, start=1):
        location = f"faq_policy[{idx}]"
        if "GO_TO:" in line and not extract_goto_targets(line):
            report.add_error(
                "UNPARSEABLE_GOTO",
                location,
                "La línea contiene GO_TO:, pero no se pudo extraer un target válido.",
            )
        for target in extract_goto_targets(line):
            _validate_target(target, location, report, known_state_ids, dynamic_slots)


def _validate_tools_and_contracts(spec: AgentSpec, report: ValidationReport) -> None:
    """Validate that EXECUTE references declared tools with valid contracts."""
    declared_tools = set(spec.tools)
    contracts = tool_contract_map(spec)
    used_tools: set[str] = set()

    for category, obj_id, obj in iter_all_flow_objects(spec):
        if not obj.execute:
            continue

        used_tools.add(obj.execute)

        if obj.execute not in declared_tools:
            report.add_error(
                "UNDECLARED_TOOL",
                f"{category}.{obj_id}.execute",
                f"La tool {obj.execute!r} no está declarada en tools.yaml.",
            )

        if obj.execute not in contracts:
            report.add_error(
                "MISSING_TOOL_CONTRACT",
                f"{category}.{obj_id}.execute",
                f"La tool {obj.execute!r} no tiene contrato en tool_contracts.yaml.",
            )
            continue

        contract = contracts[obj.execute]
        contract_outputs = {f.name for f in contract.outputs}
        normalized_capture_slots = {_canonical_slot_name(c.slot) for c in obj.capture}

        if obj.type == "action" and contract_outputs and not normalized_capture_slots:
            report.add_warning(
                "ACTION_WITHOUT_CAPTURE",
                f"{category}.{obj_id}",
                f"El estado ejecuta la tool {obj.execute!r}, que declara "
                f"outputs, pero el estado no captura ninguno.",
            )

        extra_capture = normalized_capture_slots - contract_outputs
        if obj.type == "action" and extra_capture:
            report.add_warning(
                "CAPTURE_NOT_IN_CONTRACT_OUTPUTS",
                f"{category}.{obj_id}",
                f"El estado captura slots {sorted(extra_capture)!r} que no "
                f"aparecen como outputs del contrato de {obj.execute!r}.",
            )

    for tool_name in sorted(declared_tools - used_tools):
        report.add_warning(
            "UNUSED_TOOL",
            "tools.yaml",
            f"La tool {tool_name!r} está declarada pero no se usa en ningún estado o handler.",
        )

    for contract_name in sorted(set(contracts.keys()) - declared_tools):
        report.add_warning(
            "ORPHAN_TOOL_CONTRACT",
            "tool_contracts.yaml",
            f"Existe un contrato para {contract_name!r}, pero esa tool no "
            f"está declarada en tools.yaml.",
        )


def _validate_placeholders_and_memory_slots(
    spec: AgentSpec, report: ValidationReport
) -> None:
    """Verify every ``{{var}}`` ``<CONST>`` ``[slot]`` reference is declared."""
    declared_vars = {item.name for item in spec.input_variables}
    declared_consts = {item.name for item in spec.constants}
    declared_slots = {slot.name for slot in spec.memory_slots}
    used_slots: set[str] = set()

    for slot_name in spec.manifest.dynamic_state_slots:
        if slot_name not in declared_slots:
            report.add_error(
                "DYNAMIC_SLOT_NOT_DECLARED",
                "manifest.dynamic_state_slots",
                f"El dynamic state slot {slot_name!r} no está declarado en "
                f"memory_slots.yaml o en un include.",
            )

    for category, obj_id, obj in iter_all_flow_objects(spec):
        for cap in obj.capture:
            used_slots.add(cap.slot)
            if cap.slot not in declared_slots:
                report.add_error(
                    "CAPTURE_SLOT_NOT_DECLARED",
                    f"{category}.{obj_id}.capture",
                    f"El slot capturado {cap.slot!r} no existe entre los memory slots agregados.",
                )

        for idx, line in enumerate(obj.store, start=1):
            for slot_name in extract_assignment_slots(line):
                used_slots.add(slot_name)
                if slot_name not in declared_slots:
                    report.add_error(
                        "STORE_SLOT_NOT_DECLARED",
                        f"{category}.{obj_id}.store[{idx}]",
                        f"El slot {slot_name!r} usado en STORE no existe entre "
                        f"los memory slots agregados.",
                    )

    for location, text in iter_text_fields(spec):
        for var_name in extract_variables(text):
            if var_name not in declared_vars:
                report.add_error(
                    "UNDECLARED_RUNTIME_VARIABLE",
                    location,
                    f"Se referencia la variable runtime {var_name!r}, "
                    f"pero no existe en input_variables.yaml.",
                )

        for const_name in extract_constants(text):
            if const_name not in declared_consts:
                report.add_error(
                    "UNDECLARED_CONSTANT",
                    location,
                    f"Se referencia la constante {const_name!r}, "
                    f"pero no existe en constants.yaml.",
                )

        for slot_name in extract_slots(text):
            used_slots.add(slot_name)
            if slot_name not in declared_slots:
                report.add_error(
                    "UNDECLARED_MEMORY_SLOT",
                    location,
                    f"Se referencia el memory slot {slot_name!r}, "
                    f"pero no existe entre los memory slots agregados.",
                )

    for slot_name in sorted(declared_slots - used_slots):
        report.add_warning(
            "UNUSED_MEMORY_SLOT",
            "memory_slots",
            f"El memory slot {slot_name!r} está declarado pero no se usa "
            f"en el spec agregado.",
        )


def _validate_summary_coverage(spec: AgentSpec, report: ValidationReport) -> None:
    """Verify ``context.summary_services_library`` covers ``input_variables.procedure``."""
    procedure_var = next(
        (item for item in spec.input_variables if item.name == "procedure"),
        None,
    )
    if not procedure_var or not procedure_var.allowed_values:
        return

    expected = set(procedure_var.allowed_values)
    actual = {item.procedure for item in spec.context.summary_services_library}

    missing = sorted(expected - actual)
    extra = sorted(actual - expected)

    if missing:
        report.add_error(
            "MISSING_SERVICE_SUMMARIES",
            "context.yaml",
            f"Faltan summaries para los procedures: {missing}",
        )

    if extra:
        report.add_warning(
            "EXTRA_SERVICE_SUMMARIES",
            "context.yaml",
            f"Hay summaries para procedures no declarados en "
            f"input_variables.procedure.allowed_values: {extra}",
        )


def _validate_faq_match_collisions(spec: AgentSpec, report: ValidationReport) -> None:
    """Warn when the same FAQ ``MATCH`` phrase appears in more than one FAQ."""
    phrase_map: dict[str, set[str]] = defaultdict(set)
    for faq in spec.faqs:
        for phrase in faq.match:
            phrase_map[normalize_phrase(phrase)].add(faq.faq_id)

    for phrase, owners in phrase_map.items():
        if len(owners) > 1:
            report.add_warning(
                "FAQ_MATCH_COLLISION",
                "faqs",
                f'La frase de matching "{phrase}" aparece en múltiples FAQs: '
                f"{sorted(owners)}.",
            )


def _build_graph(spec: AgentSpec) -> tuple[nx.DiGraph, str]:
    """Build the reachability graph used by the cycle/orphan analyses."""
    graph: nx.DiGraph = nx.DiGraph()
    root = "__ROOT__"
    graph.add_node(root, kind="root")

    all_state_ids = set(spec.all_state_ids)

    for handler in spec.handlers:
        graph.add_node(handler.handler_id, kind="handler")
        graph.add_edge(root, handler.handler_id)

    for state in spec.states:
        graph.add_node(state.state_id, kind="state")
    for state in spec.terminal_states:
        graph.add_node(state.state_id, kind="terminal_state")

    if spec.manifest.start_at in all_state_ids:
        graph.add_edge(root, spec.manifest.start_at)

    for faq_entry_target in sorted(_faq_entry_targets(spec)):
        graph.add_edge(root, faq_entry_target)

    for _category, obj_id, obj in iter_all_flow_objects(spec):
        for line in obj.route + obj.fallback:
            for target in extract_goto_targets(line):
                if not is_dynamic_target(target) and target in all_state_ids:
                    graph.add_edge(obj_id, target)

    return graph, root


def _validate_reachability_and_cycles(
    spec: AgentSpec, report: ValidationReport
) -> None:
    """Detect unreachable states, missing terminals and dead-end cycles."""
    all_state_ids = set(spec.all_state_ids)
    terminal_ids = terminal_state_ids(spec)

    if not terminal_ids:
        report.add_error(
            "NO_TERMINAL_STATES",
            "flow",
            "El flujo no tiene ningún estado terminal.",
        )
        return

    graph, root = _build_graph(spec)
    reachable = nx.descendants(graph, root) | {root}

    for state_id in sorted(all_state_ids):
        if state_id not in reachable:
            report.add_error(
                "UNREACHABLE_STATE",
                state_id,
                f"El estado {state_id!r} no es alcanzable. "
                f"Fuente: {_sources_for(spec, state_id)}",
            )

    reachable_terminals = sorted(terminal_ids & reachable)
    if not reachable_terminals:
        report.add_error(
            "NO_REACHABLE_TERMINAL",
            "flow",
            "No existe ningún estado terminal alcanzable desde el flujo.",
        )

    state_graph: nx.DiGraph = nx.DiGraph()
    for state_id in all_state_ids:
        state_graph.add_node(state_id)
    for state in iter_state_objects(spec):
        for line in state.route + state.fallback:
            for target in extract_goto_targets(line):
                if not is_dynamic_target(target) and target in all_state_ids:
                    state_graph.add_edge(state.state_id, target)

    states_with_dynamic_exit_path = _states_with_dynamic_exit_path(spec, state_graph)

    for state_id in sorted(all_state_ids):
        if state_id in terminal_ids:
            continue

        has_static_terminal_path = any(
            nx.has_path(state_graph, state_id, t)
            for t in terminal_ids
            if t in state_graph
        )

        if not has_static_terminal_path and state_id not in states_with_dynamic_exit_path:
            report.add_warning(
                "NO_STATIC_PATH_TO_TERMINAL",
                state_id,
                "El análisis estático no encontró un camino a un terminal "
                "desde este estado. Puede ser válido si depende de GO_TO "
                "dinámicos, pero conviene revisarlo.",
            )

    for scc in nx.strongly_connected_components(state_graph):
        if len(scc) == 1:
            node = next(iter(scc))
            if not state_graph.has_edge(node, node):
                continue

        if any(node in terminal_ids for node in scc):
            continue

        can_reach_safe_exit = False
        for node in scc:
            has_terminal_path = any(
                nx.has_path(state_graph, node, t)
                for t in terminal_ids
                if t in state_graph
            )
            if has_terminal_path or node in states_with_dynamic_exit_path:
                can_reach_safe_exit = True
                break

        if not can_reach_safe_exit:
            report.add_warning(
                "SUSPICIOUS_CYCLE",
                ",".join(sorted(scc)),
                "Se detectó un ciclo fuertemente conectado sin salida "
                "estática hacia un terminal ni acceso a un estado con "
                "salida dinámica válida.",
            )


def _extract_unconditional_single_target(lines: list[str]) -> str | None:
    """Return the only static GO_TO target if ``lines`` is a single ``GO_TO: …`` line."""
    if len(lines) != 1:
        return None
    line = lines[0].strip()
    if not line.startswith("GO_TO:"):
        return None
    targets = extract_goto_targets(line)
    if len(targets) != 1:
        return None
    return targets[0]


def _validate_redundant_route_fallback(
    spec: AgentSpec, report: ValidationReport
) -> None:
    """Warn when ``ROUTE`` and ``FALLBACK`` always send to the same target."""
    for category, obj_id, obj in iter_all_flow_objects(spec):
        route_target = _extract_unconditional_single_target(obj.route)
        fallback_target = _extract_unconditional_single_target(obj.fallback)
        if route_target and fallback_target and route_target == fallback_target:
            report.add_warning(
                "REDUNDANT_FALLBACK",
                f"{category}.{obj_id}",
                f"ROUTE y FALLBACK envían siempre al mismo target {route_target!r}.",
            )


def _validate_question_self_loops(spec: AgentSpec, report: ValidationReport) -> None:
    """Warn on question states that loop to themselves without a retry counter."""
    for state in spec.states + spec.terminal_states:
        if state.type != "question":
            continue

        targets: list[str] = []
        for line in state.route + state.fallback:
            targets.extend(extract_goto_targets(line))

        if state.state_id in targets:
            blob = " ".join(state.do + state.store + state.route + state.fallback).lower()
            if "_retry_count" not in blob:
                report.add_warning(
                    "QUESTION_SELF_LOOP_WITHOUT_RETRY_COUNTER",
                    state.state_id,
                    "El estado se reintenta a sí mismo, pero no se detectó "
                    "un retry counter explícito.",
                )


# ---------------------------------------------------------------------------
# Compliance checkers and registry
# ---------------------------------------------------------------------------


# Spanish patterns for medical compliance — case-insensitive, word-boundary
# anchored where possible to avoid false positives on substrings.
_DIAGNOSTIC_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bgarantizamos\b", re.IGNORECASE),
    re.compile(r"\bcon certeza\b", re.IGNORECASE),
    re.compile(r"\bdefinitivamente\b", re.IGNORECASE),
    re.compile(r"\bte curar", re.IGNORECASE),
    re.compile(r"\bquedarás embarazada\b", re.IGNORECASE),
    re.compile(r"\b100% efectivo\b", re.IGNORECASE),
    re.compile(r"\bsin riesgo\b", re.IGNORECASE),
)

_PRICE_GUARANTEE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bgarantizado\b", re.IGNORECASE),
    re.compile(r"\basegurado\b", re.IGNORECASE),
    re.compile(r"\bcon seguridad\b", re.IGNORECASE),
)

_DISCLAIMER_KEYWORDS: tuple[str, ...] = (
    "privacidad",
    "aviso legal",
    "términos",
    "datos personales",
    "consentimiento",
)


def _emit_compliance_issue(
    report: ValidationReport,
    rule: ComplianceRuleDefinition,
    location: str,
    detail: str,
) -> None:
    """Add a ``COMPLIANCE_VIOLATION`` issue at the rule's severity level."""
    message = f"[{rule.rule_id}] {detail}"
    if rule.severity == ComplianceSeverity.ERROR:
        report.add_error("COMPLIANCE_VIOLATION", location, message)
    else:
        report.add_warning("COMPLIANCE_VIOLATION", location, message)


def _check_no_diagnostic_promises(
    spec: AgentSpec,
    rule: ComplianceRuleDefinition,
    report: ValidationReport,
) -> None:
    """SAY blocks may not contain diagnostic promises (cure, certainty, etc.)."""
    for category, obj_id, obj in iter_all_flow_objects(spec):
        for idx, line in enumerate(obj.say, start=1):
            for pattern in _DIAGNOSTIC_PATTERNS:
                if pattern.search(line):
                    _emit_compliance_issue(
                        report,
                        rule,
                        f"{category}.{obj_id}.say[{idx}]",
                        f"El bloque SAY contiene un patrón de promesa "
                        f"diagnóstica ({pattern.pattern}): {line!r}",
                    )
                    break


def _check_requires_disclaimer_node(
    spec: AgentSpec,
    rule: ComplianceRuleDefinition,
    report: ValidationReport,
) -> None:
    """At least one node must mention privacy/disclaimer keywords in goal/do."""
    for _category, _obj_id, obj in iter_all_flow_objects(spec):
        text_blob = " ".join(obj.goal + obj.do).lower()
        if any(keyword in text_blob for keyword in _DISCLAIMER_KEYWORDS):
            return  # Found one — short-circuit.

    _emit_compliance_issue(
        report,
        rule,
        "agent",
        "Ningún estado o handler menciona keywords de disclaimer "
        f"({list(_DISCLAIMER_KEYWORDS)}) en su goal o do.",
    )


def _check_no_price_guarantees(
    spec: AgentSpec,
    rule: ComplianceRuleDefinition,
    report: ValidationReport,
) -> None:
    """SAY blocks may not guarantee prices/results (garantizado, asegurado, etc.)."""
    for category, obj_id, obj in iter_all_flow_objects(spec):
        for idx, line in enumerate(obj.say, start=1):
            for pattern in _PRICE_GUARANTEE_PATTERNS:
                if pattern.search(line):
                    _emit_compliance_issue(
                        report,
                        rule,
                        f"{category}.{obj_id}.say[{idx}]",
                        f"El bloque SAY contiene un patrón de garantía "
                        f"({pattern.pattern}): {line!r}",
                    )
                    break


def _check_scope_rules_required(
    spec: AgentSpec,
    rule: ComplianceRuleDefinition,
    report: ValidationReport,
) -> None:
    """``compliance_and_scope_rules`` policy section must be non-empty."""
    section = spec.policies.get_section("compliance_and_scope_rules")
    if not section:
        _emit_compliance_issue(
            report,
            rule,
            "policies.compliance_and_scope_rules",
            "La sección compliance_and_scope_rules está vacía o ausente.",
        )


# Registry: maps the ``check`` field of a ComplianceRuleDefinition to the
# checker function. To add a new rule, write a checker and append it here.
_COMPLIANCE_CHECKERS: dict[
    str,
    Callable[[AgentSpec, ComplianceRuleDefinition, ValidationReport], None],
] = {
    "no_diagnostic_promises": _check_no_diagnostic_promises,
    "requires_disclaimer_node": _check_requires_disclaimer_node,
    "no_price_guarantees": _check_no_price_guarantees,
    "scope_rules_required": _check_scope_rules_required,
}


def _get_compliance_checker(
    check_name: str,
) -> Callable[[AgentSpec, ComplianceRuleDefinition, ValidationReport], None]:
    """Look up a checker by name; raise if it's not registered."""
    if check_name not in _COMPLIANCE_CHECKERS:
        raise RuntimeError(
            f"Compliance check {check_name!r} no está registrado. "
            f"Disponibles: {sorted(_COMPLIANCE_CHECKERS.keys())}"
        )
    return _COMPLIANCE_CHECKERS[check_name]


def validate_compliance(
    spec: AgentSpec,
    profile: ComplianceProfile,
    report: ValidationReport,
) -> None:
    """Run every rule defined by ``profile`` against ``spec``."""
    for rule_def in profile.rules:
        checker = _get_compliance_checker(rule_def.check)
        checker(spec, rule_def, report)


# ---------------------------------------------------------------------------
# Orphan-state report (separate artifact)
# ---------------------------------------------------------------------------


def build_orphan_state_report(spec: AgentSpec) -> str:
    """Produce a Markdown report of unreachable / orphan states.

    The report is written to its own file because it is verbose and
    primarily useful for design reviews — not all entries are blocking
    errors (some legitimately rely on dynamic GO_TO).
    """
    graph, root = _build_graph(spec)
    all_state_ids = sorted(spec.all_state_ids)
    terminal_ids = terminal_state_ids(spec)
    reachable = nx.descendants(graph, root) | {root}

    entry_points = {spec.manifest.start_at} | _faq_entry_targets(spec)

    unreachable: list[tuple[str, list[str]]] = []
    no_static_incoming: list[tuple[str, list[str]]] = []
    unreachable_terminals: list[tuple[str, list[str]]] = []

    for state_id in all_state_ids:
        preds = sorted(p for p in graph.predecessors(state_id) if p != root)

        if state_id not in reachable:
            unreachable.append((state_id, preds))

        if not preds and state_id not in entry_points:
            no_static_incoming.append((state_id, preds))

        if state_id in terminal_ids and state_id not in reachable:
            unreachable_terminals.append((state_id, preds))

    def _render_table(
        rows: list[tuple[str, list[str]]], title: str
    ) -> list[str]:
        lines = [f"## {title}", ""]
        if not rows:
            lines.append("- None")
            lines.append("")
            return lines

        lines.extend(
            [
                "| State ID | Kind | Source | Explicit predecessors |",
                "| :--- | :--- | :--- | :--- |",
            ]
        )
        for state_id, preds in rows:
            kind = "terminal" if state_id in terminal_ids else "state"
            sources = spec.object_sources.get(state_id, ["unknown_source"])
            source_str = "<br>".join(sources)
            preds_str = ", ".join(preds) if preds else "-"
            lines.append(
                f"| {state_id} | {kind} | {source_str} | {preds_str} |"
            )
        lines.append("")
        return lines

    lines = [
        "# Orphan State Report",
        "",
        f"- Agent: `{spec.manifest.agent_id}`",
        f"- Total states: {len(all_state_ids)}",
        f"- Unreachable states: {len(unreachable)}",
        f"- States with no static incoming edges: {len(no_static_incoming)}",
        f"- Unreachable terminal states: {len(unreachable_terminals)}",
        "",
        "> Notes",
        "> - `No static incoming edges` means no explicit static predecessor "
        "was found in `ROUTE`, `FALLBACK`, or `FAQ_POLICY`.",
        "> - Dynamic targets such as `GO_TO: [resume_state]` are **not** "
        "counted as explicit predecessors.",
        "> - `START_AT` and any static FAQ entry states discovered from "
        "`FAQ_POLICY` are treated as valid entry states, not as orphans.",
        "",
        "## Valid entry states",
        "",
    ]

    for state_id in sorted(entry_points):
        lines.append(f"- `{state_id}`")
    lines.append("")

    lines.extend(_render_table(unreachable, "Unreachable states"))
    lines.extend(_render_table(no_static_incoming, "States with no static incoming edges"))
    lines.extend(_render_table(unreachable_terminals, "Unreachable terminal states"))

    return "\n".join(lines)