"""Disk → ``AgentSpec`` assembly pipeline plus profile loaders.

This module reads everything from disk that the rest of the compiler needs:

- ``load_channel_profile(name)`` and ``load_compliance_profile(profile_id)``
  load a profile YAML from ``profiles/{channels,compliance}/`` (resolved
  relative to the project root).
- ``load_agent_spec(agent_dir, params, channel_profile)`` is the main entry
  point. It reads the manifest and every top-level YAML, merges shared
  includes and instantiated subflows, performs alias resolution, and
  returns a fully merged :class:`AgentSpec`.

Internal helpers do path resolution (``_resolve_path``), YAML loading
(``_read_yaml``, ``_load_model``), fragment merging
(``_merge_policy_fragments`` against the channel profile,
``_merge_tool_fragments``, ``_merge_tool_contract_fragments``), subflow
instantiation (``_instantiate_subflow``) and the final alias-resolution
pass (``_resolve_aliases_in_*``).

The legacy ``flow/`` subdirectory and the hardcoded ``POLICY_SECTION_NAMES``
tuple are gone in v2: agent local content lives in the eight top-level
YAMLs plus reusable subflow templates under ``subflows/``, and policy
section names come from the active ``ChannelProfile``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from app.schemas import (
    AgentSpec,
    ChannelProfile,
    ComplianceProfile,
    CompilationParams,
    ConstantsFile,
    ContextFile,
    FAQPolicyFile,
    FAQsFile,
    FlowRulesFile,
    HandlersFile,
    IdentityFile,
    InputVariablesFile,
    ManifestConfig,
    MemorySlot,
    MemorySlotsFile,
    ObjectivesFile,
    PoliciesFile,
    PoliciesFragmentFile,
    StatesFile,
    SubflowInstanceRef,
    SubflowTemplate,
    TerminalStatesFile,
    ToolContractsFile,
    ToolContractsFragmentFile,
    ToolsFile,
    ToolsFragmentFile,
)
from app.utils import (
    dedupe_preserve_order,
    namespace_slot_name,
    namespace_state_id,
    resolve_slot_aliases,
    resolve_state_alias_targets,
    resolve_state_alias_value,
    rewrite_local_goto_targets,
    rewrite_local_slots_in_text,
    substitute_params,
)

T = TypeVar("T", bound=BaseModel)


# ---------------------------------------------------------------------------
# Path / YAML helpers
# ---------------------------------------------------------------------------


def _project_root() -> Path:
    """Return the project root, computed from this file's location."""
    return Path(__file__).resolve().parent.parent


def _ensure_exists(path: Path) -> None:
    """Raise ``FileNotFoundError`` if ``path`` does not exist."""
    if not path.exists():
        raise FileNotFoundError(f"No existe el archivo requerido: {path}")


def _read_yaml(path: Path) -> dict[str, Any]:
    """Read ``path`` as UTF-8 YAML and return its top-level mapping."""
    _ensure_exists(path)
    try:
        raw = path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise RuntimeError(f"Error parseando YAML en {path}:\n{exc}") from exc

    if data is None:
        data = {}

    if not isinstance(data, dict):
        raise RuntimeError(
            f"El archivo {path} debe tener un mapping YAML en la raíz."
        )
    return data


def _load_model(path: Path, model_cls: type[T]) -> T:
    """Read ``path`` as YAML and validate it through ``model_cls``."""
    data = _read_yaml(path)
    try:
        return model_cls.model_validate(data)
    except ValidationError as exc:
        raise RuntimeError(f"Error validando {path}:\n{exc}") from exc


def _resolve_path(agent_dir: Path, raw_path: str) -> Path:
    """Resolve ``raw_path`` as absolute, relative to ``agent_dir``, or project root."""
    path = Path(raw_path)
    if path.is_absolute():
        return path

    # Intenta primero relativo al directorio del agente (comportamiento estándar)
    candidate_local = (agent_dir / path).resolve()
    if candidate_local.exists():
        return candidate_local

    # Intenta relativo a la raíz del proyecto (útil para rutas que empiezan con configs/ o shared/)
    candidate_root = (_project_root() / path).resolve()
    if candidate_root.exists():
        return candidate_root

    # Si ninguno existe, devuelve el local para que el error muestre la ruta esperada
    return candidate_local


# ---------------------------------------------------------------------------
# Profile loaders
# ---------------------------------------------------------------------------


def load_channel_profile(profile_name: str) -> ChannelProfile:
    """Load ``profiles/channels/{profile_name}.yaml`` from the project root."""
    path = _project_root() / "profiles" / "channels" / f"{profile_name}.yaml"
    return _load_model(path, ChannelProfile)


def load_compliance_profile(profile_id: str | None) -> ComplianceProfile | None:
    """Load ``profiles/compliance/{profile_id}.yaml`` from the project root.

    Returns ``None`` if ``profile_id`` is ``None`` (compliance is opt-in).
    """
    if profile_id is None:
        return None
    path = _project_root() / "profiles" / "compliance" / f"{profile_id}.yaml"
    return _load_model(path, ComplianceProfile)


# ---------------------------------------------------------------------------
# Provenance tracking and include loaders
# ---------------------------------------------------------------------------


def _append_object_sources(
    object_sources: dict[str, list[str]],
    items: Iterable[Any],
    id_attr: str,
    source_path: str,
) -> None:
    """Record that each item's id was contributed by ``source_path``."""
    for item in items:
        object_id = getattr(item, id_attr)
        object_sources.setdefault(object_id, []).append(source_path)


def _load_include_texts(
    agent_dir: Path,
    paths: list[str],
    model_cls: type[T],
    attr_name: str,
) -> list[str]:
    """Load a list of text-list YAMLs (flow_rules, faq_policy) and concatenate them."""
    result: list[str] = []
    for raw_path in paths:
        path = _resolve_path(agent_dir, raw_path)
        model = _load_model(path, model_cls)
        result.extend(getattr(model, attr_name))
    return result


def _load_include_objects(
    agent_dir: Path,
    paths: list[str],
    model_cls: type[T],
    attr_name: str,
    id_attr: str,
    object_sources: dict[str, list[str]],
) -> list[Any]:
    """Load a list of object YAMLs (handlers, faqs, states, terminal_states).

    Provenance of each contributed object is recorded in ``object_sources``.
    """
    result: list[Any] = []
    for raw_path in paths:
        path = _resolve_path(agent_dir, raw_path)
        model = _load_model(path, model_cls)
        items = getattr(model, attr_name)
        result.extend(items)
        _append_object_sources(object_sources, items, id_attr, path.as_posix())
    return result


def _load_include_memory_slots(
    agent_dir: Path, paths: list[str]
) -> list[MemorySlot]:
    """Load shared memory_slots fragments and concatenate them."""
    result: list[MemorySlot] = []
    for raw_path in paths:
        path = _resolve_path(agent_dir, raw_path)
        model = _load_model(path, MemorySlotsFile)
        result.extend(model.memory_slots)
    return result


# ---------------------------------------------------------------------------
# Fragment merging
# ---------------------------------------------------------------------------


def _merge_policy_fragments(
    fragments: list[PoliciesFragmentFile],
    channel_profile: ChannelProfile,
) -> PoliciesFile:
    """Merge policy fragments, validating section names against the channel profile.

    - Sections allowed are exclusively those declared in
      ``channel_profile.policy_sections``.
    - Fragments contributing an unknown section trigger a ``RuntimeError``.
    - Required sections must contain at least one rule after merging.
    """
    section_names = [s.name for s in channel_profile.policy_sections]
    valid_section_set = set(section_names)
    required_section_names = {s.name for s in channel_profile.policy_sections if s.required}

    merged: dict[str, list[str]] = {name: [] for name in section_names}

    for fragment in fragments:
        for key, value in fragment.all_sections().items():
            if key not in valid_section_set:
                raise RuntimeError(
                    f"La sección de políticas {key!r} no es válida para el canal "
                    f"{channel_profile.channel.value!r}. "
                    f"Secciones permitidas: {sorted(valid_section_set)}."
                )
            merged[key].extend(value or [])

    for name in section_names:
        merged[name] = dedupe_preserve_order(merged[name])

    for name in required_section_names:
        if not merged[name]:
            raise RuntimeError(
                f"La sección de políticas {name!r} es requerida para el canal "
                f"{channel_profile.channel.value!r} pero está vacía después del merge."
            )

    return PoliciesFile.model_validate(merged)


def _merge_tool_fragments(fragments: list[ToolsFragmentFile]) -> ToolsFile:
    """Concatenate tool name fragments and dedupe while preserving order."""
    merged_tools: list[str] = []
    for fragment in fragments:
        merged_tools.extend(fragment.tools)
    merged_tools = dedupe_preserve_order(merged_tools)
    return ToolsFile.model_validate({"tools": merged_tools})


def _merge_tool_contract_fragments(
    fragments_with_source: list[tuple[str, ToolContractsFragmentFile]],
) -> ToolContractsFile:
    """Concatenate tool contract fragments. Duplicate tool names are an error.

    Each fragment is paired with a source label so error messages can point
    to the file that defined the conflicting contract.
    """
    merged_contracts = []
    seen_sources: dict[str, str] = {}

    for source_label, fragment in fragments_with_source:
        for contract in fragment.tool_contracts:
            if contract.name in seen_sources:
                raise RuntimeError(
                    f"El tool contract {contract.name!r} está duplicado. "
                    f"Primera definición: {seen_sources[contract.name]}. "
                    f"Duplicado: {source_label}."
                )
            seen_sources[contract.name] = source_label
            merged_contracts.append(contract)

    return ToolContractsFile.model_validate({"tool_contracts": merged_contracts})


# ---------------------------------------------------------------------------
# Subflow template instantiation
# ---------------------------------------------------------------------------


def _materialize_template_params(
    instance: SubflowInstanceRef, template: SubflowTemplate
) -> dict[str, str]:
    """Produce the concrete params dict for ``instance`` against ``template``.

    - Unknown params (not declared by the template) → ``RuntimeError``.
    - Missing required params with no default → ``RuntimeError``.
    """
    defs = {item.name: item for item in template.params}
    unknown_params = set(instance.params.keys()) - set(defs.keys())
    if unknown_params:
        raise RuntimeError(
            f"La instancia {instance.instance_id!r} usa parámetros desconocidos "
            f"para el template {template.template_id!r}: {sorted(unknown_params)}"
        )

    result: dict[str, str] = {}
    for name, definition in defs.items():
        if name in instance.params:
            result[name] = instance.params[name]
        elif definition.default is not None:
            result[name] = definition.default
        elif definition.required:
            raise RuntimeError(
                f"La instancia {instance.instance_id!r} requiere el parámetro "
                f"{name!r} para el template {template.template_id!r}."
            )

    return result


def _transform_lines(
    lines: list[str],
    params: dict[str, str],
    local_slot_map: dict[str, str],
    local_state_map: dict[str, str],
    *,
    rewrite_targets: bool = False,
) -> list[str]:
    """Apply param substitution + slot rewriting (+ optional GO_TO rewriting)."""
    result = []
    for line in lines:
        new_line = substitute_params(line, params)
        new_line = rewrite_local_slots_in_text(new_line, local_slot_map)
        if rewrite_targets:
            new_line = rewrite_local_goto_targets(new_line, local_state_map)
        result.append(new_line)
    return result


def _transform_flow_object(
    obj: Any,
    obj_id_attr: str,
    obj_id_map: dict[str, str],
    local_slot_map: dict[str, str],
    local_state_map: dict[str, str],
    params: dict[str, str],
) -> Any:
    """Deep-copy ``obj`` and rewrite its id, line lists, captures and faq_resume_to."""
    new_obj = obj.model_copy(deep=True)
    original_id = getattr(new_obj, obj_id_attr)
    setattr(new_obj, obj_id_attr, obj_id_map[original_id])

    new_obj.goal = _transform_lines(new_obj.goal, params, local_slot_map, local_state_map)
    new_obj.do = _transform_lines(new_obj.do, params, local_slot_map, local_state_map)
    new_obj.say = _transform_lines(new_obj.say, params, local_slot_map, local_state_map)

    for cap in new_obj.capture:
        if cap.slot in local_slot_map:
            cap.slot = local_slot_map[cap.slot]

    new_obj.store = _transform_lines(
        new_obj.store, params, local_slot_map, local_state_map
    )
    new_obj.route = _transform_lines(
        new_obj.route, params, local_slot_map, local_state_map, rewrite_targets=True
    )
    new_obj.fallback = _transform_lines(
        new_obj.fallback, params, local_slot_map, local_state_map, rewrite_targets=True
    )

    if getattr(new_obj, "trigger", None):
        new_obj.trigger = _transform_lines(
            new_obj.trigger, params, local_slot_map, local_state_map
        )

    if new_obj.faq_resume_to:
        resume_target = substitute_params(new_obj.faq_resume_to, params)
        if resume_target in local_state_map:
            resume_target = local_state_map[resume_target]
        new_obj.faq_resume_to = resume_target

    return new_obj


def _transform_faq(
    obj: Any,
    faq_id_map: dict[str, str],
    local_slot_map: dict[str, str],
    params: dict[str, str],
) -> Any:
    """Deep-copy a FAQ and apply param substitution + slot rewriting."""
    new_obj = obj.model_copy(deep=True)
    new_obj.faq_id = faq_id_map[new_obj.faq_id]
    new_obj.match = [
        rewrite_local_slots_in_text(substitute_params(x, params), local_slot_map)
        for x in new_obj.match
    ]
    new_obj.answer = [
        rewrite_local_slots_in_text(substitute_params(x, params), local_slot_map)
        for x in new_obj.answer
    ]
    return new_obj


def _instantiate_subflow(
    agent_dir: Path,
    instance: SubflowInstanceRef,
    declared_tools: set[str],
    declared_tool_contracts: set[str],
) -> dict[str, Any]:
    """Materialize a subflow template into namespaced flow objects.

    Steps:
    1. Resolve the template path and load it.
    2. Verify every ``required_tool`` is declared and has a contract.
    3. Materialize params (validates required, applies defaults).
    4. Build local→namespaced maps for state ids, handler ids, faq ids, slots.
    5. Deep-copy and rewrite every flow object via ``_transform_flow_object``.
    6. Build state and slot export maps for downstream alias resolution.
    """
    template_path = _resolve_path(agent_dir, instance.template)
    template = _load_model(template_path, SubflowTemplate)

    for tool_name in template.required_tools:
        if tool_name not in declared_tools:
            raise RuntimeError(
                f"La instancia {instance.instance_id!r} requiere la tool "
                f"{tool_name!r} por el template {template.template_id!r}, "
                f"pero no existe en tools.yaml."
            )
        if tool_name not in declared_tool_contracts:
            raise RuntimeError(
                f"La instancia {instance.instance_id!r} requiere la tool "
                f"{tool_name!r} por el template {template.template_id!r}, "
                f"pero no existe contrato en tool_contracts.yaml."
            )

    params = _materialize_template_params(instance, template)

    local_state_ids = {s.state_id for s in template.states} | {
        s.state_id for s in template.terminal_states
    }
    local_handler_ids = {h.handler_id for h in template.handlers}
    local_faq_ids = {f.faq_id for f in template.faqs}
    local_slot_names = {s.name for s in template.local_memory_slots}

    namespace = instance.namespace or instance.instance_id
    local_state_map = {item: namespace_state_id(namespace, item) for item in local_state_ids}
    local_handler_map = {
        item: namespace_state_id(namespace, item) for item in local_handler_ids
    }
    local_faq_map = {item: namespace_state_id(namespace, item) for item in local_faq_ids}
    local_slot_map = {
        item: namespace_slot_name(namespace, item) for item in local_slot_names
    }

    instantiated_memory_slots: list[MemorySlot] = []
    for slot in template.local_memory_slots:
        new_slot = slot.model_copy(deep=True)
        new_slot.name = local_slot_map[new_slot.name]
        instantiated_memory_slots.append(new_slot)

    instantiated_flow_rules = _transform_lines(
        template.flow_rules,
        params,
        local_slot_map,
        local_state_map,
        rewrite_targets=True,
    )
    instantiated_faq_policy = _transform_lines(
        template.faq_policy,
        params,
        local_slot_map,
        local_state_map,
        rewrite_targets=True,
    )

    instantiated_handlers = [
        _transform_flow_object(
            h, "handler_id", local_handler_map, local_slot_map, local_state_map, params
        )
        for h in template.handlers
    ]
    instantiated_faqs = [
        _transform_faq(f, local_faq_map, local_slot_map, params) for f in template.faqs
    ]
    instantiated_states = [
        _transform_flow_object(
            s, "state_id", local_state_map, local_slot_map, local_state_map, params
        )
        for s in template.states
    ]
    instantiated_terminal_states = [
        _transform_flow_object(
            s, "state_id", local_state_map, local_slot_map, local_state_map, params
        )
        for s in template.terminal_states
    ]

    state_exports = {
        export_name: local_state_map[local_target]
        for export_name, local_target in template.exports.states.items()
    }
    slot_exports = {
        export_name: local_slot_map.get(slot_name, slot_name)
        for export_name, slot_name in template.exports.slots.items()
    }

    source_label = f"{template_path.as_posix()}#instance={instance.instance_id}"

    return {
        "instance_id": instance.instance_id,
        "source_label": source_label,
        "memory_slots": instantiated_memory_slots,
        "flow_rules": instantiated_flow_rules,
        "faq_policy": instantiated_faq_policy,
        "handlers": instantiated_handlers,
        "faqs": instantiated_faqs,
        "states": instantiated_states,
        "terminal_states": instantiated_terminal_states,
        "state_exports": state_exports,
        "slot_exports": slot_exports,
    }


# ---------------------------------------------------------------------------
# Final alias resolution pass (after all instances are merged)
# ---------------------------------------------------------------------------


def _resolve_aliases_in_lines(
    lines: list[str],
    flat_state_export_map: dict[str, str],
    flat_slot_export_map: dict[str, str],
    *,
    resolve_target_aliases: bool = False,
) -> list[str]:
    """Resolve ``@slot(...)`` aliases (and optionally ``GO_TO: @...`` aliases)."""
    result = []
    for line in lines:
        new_line = resolve_slot_aliases(line, flat_slot_export_map)
        if resolve_target_aliases:
            new_line = resolve_state_alias_targets(new_line, flat_state_export_map)
        result.append(new_line)
    return result


def _resolve_aliases_in_flow_object(
    obj: Any,
    flat_state_export_map: dict[str, str],
    flat_slot_export_map: dict[str, str],
) -> Any:
    """Resolve aliases in every text field of ``obj`` (handler / state / terminal)."""
    obj.goal = _resolve_aliases_in_lines(
        obj.goal, flat_state_export_map, flat_slot_export_map
    )
    obj.do = _resolve_aliases_in_lines(
        obj.do, flat_state_export_map, flat_slot_export_map
    )
    obj.say = _resolve_aliases_in_lines(
        obj.say, flat_state_export_map, flat_slot_export_map
    )
    obj.store = _resolve_aliases_in_lines(
        obj.store, flat_state_export_map, flat_slot_export_map
    )
    obj.route = _resolve_aliases_in_lines(
        obj.route,
        flat_state_export_map,
        flat_slot_export_map,
        resolve_target_aliases=True,
    )
    obj.fallback = _resolve_aliases_in_lines(
        obj.fallback,
        flat_state_export_map,
        flat_slot_export_map,
        resolve_target_aliases=True,
    )

    if getattr(obj, "trigger", None):
        obj.trigger = _resolve_aliases_in_lines(
            obj.trigger, flat_state_export_map, flat_slot_export_map
        )

    if obj.faq_resume_to:
        obj.faq_resume_to = resolve_state_alias_value(
            obj.faq_resume_to, flat_state_export_map
        )

    return obj


def _validate_no_unresolved_aliases(lines: list[str], location_prefix: str) -> None:
    """Hard guard: fail compilation if any unresolved alias survives."""
    for idx, line in enumerate(lines, start=1):
        if "@slot(" in line:
            raise RuntimeError(
                f"Quedó un alias de slot sin resolver en {location_prefix}[{idx}]: {line}"
            )
        if "GO_TO: @" in line:
            raise RuntimeError(
                f"Quedó un alias de estado sin resolver en {location_prefix}[{idx}]: {line}"
            )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def load_agent_spec(
    agent_dir: str | Path,
    params: CompilationParams,
    channel_profile: ChannelProfile,
) -> AgentSpec:
    """Assemble the :class:`AgentSpec` from disk.

    Reads the agent's eight top-level YAMLs, every shared include declared
    in the manifest, and every subflow instance. Instantiates subflows with
    namespacing and parameter substitution, runs a final alias-resolution
    pass, and verifies no alias survived unresolved.

    Args:
        agent_dir: Path to ``configs/{agent_id}/``.
        params: ``CompilationParams`` (currently informational; the channel
            is consumed via the explicit ``channel_profile`` argument).
        channel_profile: Loaded ``ChannelProfile`` matching ``params.channel``.

    Returns:
        Fully merged :class:`AgentSpec` ready for the classifier, the
        validator, the deduplicator and the renderers.

    Raises:
        FileNotFoundError, NotADirectoryError, RuntimeError: when paths are
        missing, YAML is malformed, schemas reject content, required
        policy sections are empty, parameters are missing, or aliases
        survive the resolution pass.
    """
    base = Path(agent_dir)
    if not base.exists():
        raise FileNotFoundError(f"No existe el directorio del agente: {base}")
    if not base.is_dir():
        raise NotADirectoryError(f"La ruta del agente no es un directorio: {base}")

    object_sources: dict[str, list[str]] = {}

    # Top-level files (the eight required YAMLs of any agent).
    manifest = _load_model(base / "manifest.yaml", ManifestConfig)
    constants = _load_model(base / "constants.yaml", ConstantsFile)
    identity = _load_model(base / "identity.yaml", IdentityFile)
    objectives = _load_model(base / "objectives.yaml", ObjectivesFile)
    context = _load_model(base / "context.yaml", ContextFile)
    input_variables = _load_model(base / "input_variables.yaml", InputVariablesFile)

    local_policies = _load_model(base / "policies.yaml", PoliciesFragmentFile)
    local_tools = _load_model(base / "tools.yaml", ToolsFragmentFile)
    local_tool_contracts = _load_model(
        base / "tool_contracts.yaml", ToolContractsFragmentFile
    )
    local_memory_slots_file = _load_model(base / "memory_slots.yaml", MemorySlotsFile)

    # Shared text and fragment includes declared in the manifest.
    shared_memory_slots = _load_include_memory_slots(
        base, manifest.includes.memory_slots
    )
    shared_flow_rules = _load_include_texts(
        base, manifest.includes.flow_rules, FlowRulesFile, "flow_rules"
    )
    shared_faq_policy = _load_include_texts(
        base, manifest.includes.faq_policy, FAQPolicyFile, "faq_policy"
    )

    shared_policy_fragments: list[PoliciesFragmentFile] = []
    for raw_path in manifest.includes.policies:
        path = _resolve_path(base, raw_path)
        shared_policy_fragments.append(_load_model(path, PoliciesFragmentFile))

    shared_tool_fragments: list[ToolsFragmentFile] = []
    for raw_path in manifest.includes.tools:
        path = _resolve_path(base, raw_path)
        shared_tool_fragments.append(_load_model(path, ToolsFragmentFile))

    shared_tool_contract_fragments: list[tuple[str, ToolContractsFragmentFile]] = []
    for raw_path in manifest.includes.tool_contracts:
        path = _resolve_path(base, raw_path)
        shared_tool_contract_fragments.append(
            (path.as_posix(), _load_model(path, ToolContractsFragmentFile))
        )

    # Merge fragments into final files (channel profile drives policy validation).
    policies = _merge_policy_fragments(
        shared_policy_fragments + [local_policies], channel_profile
    )
    tools = _merge_tool_fragments(shared_tool_fragments + [local_tools])
    tool_contracts = _merge_tool_contract_fragments(
        shared_tool_contract_fragments
        + [((base / "tool_contracts.yaml").as_posix(), local_tool_contracts)]
    )

    # Shared object includes (handlers, faqs, states, terminal_states).
    shared_handlers = _load_include_objects(
        base,
        manifest.includes.handlers,
        HandlersFile,
        "handlers",
        "handler_id",
        object_sources,
    )
    shared_faqs = _load_include_objects(
        base, manifest.includes.faqs, FAQsFile, "faqs", "faq_id", object_sources
    )
    shared_states = _load_include_objects(
        base,
        manifest.includes.states,
        StatesFile,
        "states",
        "state_id",
        object_sources,
    )
    shared_terminal_states = _load_include_objects(
        base,
        manifest.includes.terminal_states,
        TerminalStatesFile,
        "terminal_states",
        "state_id",
        object_sources,
    )

    # Instantiate subflows.
    declared_tools = set(tools.tools)
    declared_tool_contracts = {c.name for c in tool_contracts.tool_contracts}

    instance_state_exports: dict[str, dict[str, str]] = {}
    instance_slot_exports: dict[str, dict[str, str]] = {}

    instance_memory_slots: list[MemorySlot] = []
    instance_flow_rules: list[str] = []
    instance_faq_policy: list[str] = []
    instance_handlers: list[Any] = []
    instance_faqs: list[Any] = []
    instance_states: list[Any] = []
    instance_terminal_states: list[Any] = []

    used_namespaces: set[str] = set()
    for instance in manifest.subflow_instances:
        if instance.namespace in used_namespaces:
            raise RuntimeError(
                f"namespace duplicado en manifest.subflow_instances: "
                f"{instance.namespace!r}"
            )
        used_namespaces.add(instance.namespace)

        instantiated = _instantiate_subflow(
            base, instance, declared_tools, declared_tool_contracts
        )

        instance_state_exports[instantiated["instance_id"]] = instantiated["state_exports"]
        instance_slot_exports[instantiated["instance_id"]] = instantiated["slot_exports"]

        instance_memory_slots.extend(instantiated["memory_slots"])
        instance_flow_rules.extend(instantiated["flow_rules"])
        instance_faq_policy.extend(instantiated["faq_policy"])
        instance_handlers.extend(instantiated["handlers"])
        instance_faqs.extend(instantiated["faqs"])
        instance_states.extend(instantiated["states"])
        instance_terminal_states.extend(instantiated["terminal_states"])

        for items, id_attr in (
            (instantiated["handlers"], "handler_id"),
            (instantiated["faqs"], "faq_id"),
            (instantiated["states"], "state_id"),
            (instantiated["terminal_states"], "state_id"),
        ):
            _append_object_sources(
                object_sources, items, id_attr, instantiated["source_label"]
            )

    # Flatten exports for cross-instance alias resolution.
    flat_state_export_map = {
        f"{instance_id}.{export_name}": target
        for instance_id, exports in instance_state_exports.items()
        for export_name, target in exports.items()
    }
    flat_slot_export_map = {
        f"{instance_id}.{export_name}": slot_name
        for instance_id, exports in instance_slot_exports.items()
        for export_name, slot_name in exports.items()
    }

    # Aggregate. Order is intentional: shared first, instance content second,
    # so shared interrupts and FAQs take precedence in evaluation order.
    memory_slots = (
        list(shared_memory_slots)
        + list(local_memory_slots_file.memory_slots)
        + list(instance_memory_slots)
    )
    flow_rules = list(shared_flow_rules) + list(instance_flow_rules)
    faq_policy = list(shared_faq_policy) + list(instance_faq_policy)
    handlers = list(shared_handlers) + list(instance_handlers)
    faqs = list(shared_faqs) + list(instance_faqs)
    states = list(shared_states) + list(instance_states)
    terminal_states = list(shared_terminal_states) + list(instance_terminal_states)

    # Final alias resolution pass.
    flow_rules = _resolve_aliases_in_lines(
        flow_rules,
        flat_state_export_map,
        flat_slot_export_map,
        resolve_target_aliases=True,
    )
    faq_policy = _resolve_aliases_in_lines(
        faq_policy,
        flat_state_export_map,
        flat_slot_export_map,
        resolve_target_aliases=True,
    )

    for handler in handlers:
        _resolve_aliases_in_flow_object(
            handler, flat_state_export_map, flat_slot_export_map
        )
    for state in states:
        _resolve_aliases_in_flow_object(
            state, flat_state_export_map, flat_slot_export_map
        )
    for state in terminal_states:
        _resolve_aliases_in_flow_object(
            state, flat_state_export_map, flat_slot_export_map
        )
    for faq in faqs:
        faq.match = _resolve_aliases_in_lines(
            faq.match, flat_state_export_map, flat_slot_export_map
        )
        faq.answer = _resolve_aliases_in_lines(
            faq.answer, flat_state_export_map, flat_slot_export_map
        )

    # Hard guards: every alias must be resolved by now.
    _validate_no_unresolved_aliases(flow_rules, "flow_rules")
    _validate_no_unresolved_aliases(faq_policy, "faq_policy")

    for handler in handlers:
        _validate_no_unresolved_aliases(
            handler.route, f"handler.{handler.handler_id}.route"
        )
        _validate_no_unresolved_aliases(
            handler.fallback, f"handler.{handler.handler_id}.fallback"
        )
        for line in (
            handler.goal
            + handler.do
            + handler.say
            + handler.store
            + getattr(handler, "trigger", [])
        ):
            if "@slot(" in line:
                raise RuntimeError(
                    f"Quedó un alias de slot sin resolver en handler "
                    f"{handler.handler_id!r}: {line}"
                )
        if handler.faq_resume_to and handler.faq_resume_to.startswith("@"):
            raise RuntimeError(
                f"Quedó un alias faq_resume_to sin resolver en handler "
                f"{handler.handler_id!r}: {handler.faq_resume_to}"
            )

    for state in states + terminal_states:
        _validate_no_unresolved_aliases(state.route, f"state.{state.state_id}.route")
        _validate_no_unresolved_aliases(
            state.fallback, f"state.{state.state_id}.fallback"
        )
        for line in state.goal + state.do + state.say + state.store:
            if "@slot(" in line:
                raise RuntimeError(
                    f"Quedó un alias de slot sin resolver en state "
                    f"{state.state_id!r}: {line}"
                )
        if state.faq_resume_to and state.faq_resume_to.startswith("@"):
            raise RuntimeError(
                f"Quedó un alias faq_resume_to sin resolver en state "
                f"{state.state_id!r}: {state.faq_resume_to}"
            )

    return AgentSpec(
        manifest=manifest,
        constants=constants.constants,
        input_variables=input_variables.input_variables,
        tools=tools.tools,
        tool_contracts=tool_contracts.tool_contracts,
        memory_slots=memory_slots,
        identity=identity.identity,
        objectives=objectives,
        context=context,
        policies=policies,
        flow_rules=flow_rules,
        faq_policy=faq_policy,
        handlers=handlers,
        faqs=faqs,
        states=states,
        terminal_states=terminal_states,
        object_sources=object_sources,
        instance_state_exports=instance_state_exports,
        instance_slot_exports=instance_slot_exports,
    )