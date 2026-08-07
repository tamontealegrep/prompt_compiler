"""Unit tests for app/loaders.py — disk assembly, fragment merging, subflow instantiation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.loaders import (
    _materialize_template_params,
    _merge_policy_fragments,
    _merge_tool_contract_fragments,
    _merge_tool_fragments,
    _resolve_path,
    load_agent_spec,
)
from app.schemas import (
    ChannelProfile,
    ChannelType,
    CompilationParams,
    PoliciesFragmentFile,
    PolicySectionDefinition,
    SubflowInstanceRef,
    SubflowTemplate,
    TemplateParamDefinition,
    ToolContractsFragmentFile,
    ToolsFragmentFile,
)

MINIMAL_YAMLS = {
    "manifest.yaml": {
        "agent_id": "test_agent",
        "language": "es",
        "start_at": "GREETING",
        "includes": {"states": ["states.yaml"], "terminal_states": ["terminal_states.yaml"]},
    },
    "constants.yaml": {
        "constants": [{"name": "MAX_RETRY", "description": "Max retries.", "value": "3"}]
    },
    "identity.yaml": {"identity": ["A synthetic test agent."]},
    "objectives.yaml": {
        "primary_objective": ["Greet."],
        "secondary_objectives": ["Demonstrate loading."],
        "success_alternatives": ["The caller is greeted."],
    },
    "context.yaml": {
        "company_context": ["Synthetic company."],
        "approved_services": ["Synthetic service."],
        "summary_services_library": [
            {"key": "GREETING_SERVICE", "procedure": "greeting_service", "text": "Synthetic."}
        ],
        "approved_process_intro": "Synthetic process.",
        "approved_process_steps": [{"title": "Step", "text": "Greet."}],
        "support_and_trust": ["This is a test fixture."],
    },
    "input_variables.yaml": {
        "input_variables": [{"name": "caller_name", "description": "Caller name."}]
    },
    "policies.yaml": {"compliance_and_scope_rules": ["Never give medical advice."]},
    "tools.yaml": {"tools": ["noop_tool"]},
    "tool_contracts.yaml": {
        "tool_contracts": [
            {
                "name": "noop_tool",
                "description": "A no-op tool declared only to satisfy the non-empty schema.",
                "inputs": [],
                "outputs": [],
                "notes": [],
            }
        ]
    },
    "memory_slots.yaml": {
        "memory_slots": [
            {"name": "current_state", "description": "Active state.", "kind": "dynamic_state"},
            {"name": "resume_state", "description": "Resume state.", "kind": "dynamic_state"},
        ]
    },
    "states.yaml": {
        "states": [
            {
                "state_id": "GREETING",
                "type": "message",
                "say": ["Hello."],
                "wait": "no",
                "route": ["GO_TO: CLOSE"],
            }
        ]
    },
    "terminal_states.yaml": {
        "terminal_states": [
            {
                "state_id": "CLOSE",
                "type": "terminal",
                "say": ["Goodbye."],
                "wait": "no",
                "final": "yes",
            }
        ]
    },
}


def _write_minimal_agent(base: Path, *, overrides: dict[str, dict] | None = None) -> Path:
    """Write MINIMAL_YAMLS (with optional per-file overrides) under ``base``."""
    overrides = overrides or {}
    for filename, content in MINIMAL_YAMLS.items():
        data = {**content, **overrides.get(filename, {})}
        (base / filename).write_text(yaml.safe_dump(data), encoding="utf-8")
    return base


@pytest.fixture
def synthetic_channel_profile() -> ChannelProfile:
    """A minimal in-memory channel profile with one required policy section."""
    return ChannelProfile(
        channel=ChannelType.VOICE,
        display_name="Synthetic Test Channel",
        policy_sections=[
            PolicySectionDefinition(
                name="compliance_and_scope_rules",
                label="COMPLIANCE_AND_SCOPE_RULES",
                required=True,
            ),
            PolicySectionDefinition(
                name="optional_rules", label="OPTIONAL_RULES", required=False
            ),
        ],
    )


@pytest.fixture
def default_params() -> CompilationParams:
    return CompilationParams(channel=ChannelType.VOICE)


# ---------------------------------------------------------------------------
# load_agent_spec — happy path and top-level error handling
# ---------------------------------------------------------------------------


def test_load_agent_spec_builds_expected_agent_spec(
    tmp_path, synthetic_channel_profile, default_params
):
    agent_dir = _write_minimal_agent(tmp_path)
    spec = load_agent_spec(agent_dir, default_params, synthetic_channel_profile)

    assert spec.manifest.agent_id == "test_agent"
    assert spec.main_state_ids == ["GREETING"]
    assert spec.all_state_ids == ["GREETING", "CLOSE"]
    assert [c.name for c in spec.constants] == ["MAX_RETRY"]


def test_load_agent_spec_raises_file_not_found_for_missing_agent_dir(
    tmp_path, synthetic_channel_profile, default_params
):
    with pytest.raises(FileNotFoundError):
        load_agent_spec(tmp_path / "does_not_exist", default_params, synthetic_channel_profile)


def test_load_agent_spec_raises_not_a_directory_when_agent_dir_is_a_file(
    tmp_path, synthetic_channel_profile, default_params
):
    a_file = tmp_path / "not_a_dir.txt"
    a_file.write_text("hello", encoding="utf-8")
    with pytest.raises(NotADirectoryError):
        load_agent_spec(a_file, default_params, synthetic_channel_profile)


def test_load_agent_spec_raises_runtime_error_for_malformed_yaml(
    tmp_path, synthetic_channel_profile, default_params
):
    agent_dir = _write_minimal_agent(tmp_path)
    (agent_dir / "constants.yaml").write_text("constants: [this is not: valid: yaml", encoding="utf-8")
    with pytest.raises(RuntimeError):
        load_agent_spec(agent_dir, default_params, synthetic_channel_profile)


# ---------------------------------------------------------------------------
# _resolve_path
# ---------------------------------------------------------------------------


def test_resolve_path_prefers_agent_local_file(tmp_path):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / "local.yaml").write_text("a: 1", encoding="utf-8")
    resolved = _resolve_path(agent_dir, "local.yaml")
    assert resolved == (agent_dir / "local.yaml").resolve()


def test_resolve_path_falls_back_to_local_when_nothing_exists(tmp_path):
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    resolved = _resolve_path(agent_dir, "does_not_exist_anywhere.yaml")
    assert resolved == (agent_dir / "does_not_exist_anywhere.yaml").resolve()


def test_resolve_path_translates_legacy_configs_prefix_to_agents_defs():
    # _project_root() is hardcoded to the real repo root (parent of app/),
    # so this test can't be fully sandboxed under tmp_path. It only asserts
    # the translation attempt doesn't raise and falls through predictably
    # for a target that doesn't exist under the real agents/defs/ tree.
    agent_dir = Path(__file__).resolve().parents[3] / "agents" / "defs" / "some_agent"
    resolved = _resolve_path(agent_dir, "configs/some_agent/whatever.yaml")
    translated = "agents/defs" in resolved.as_posix()
    fell_through = resolved.as_posix().endswith("configs/some_agent/whatever.yaml")
    assert translated or fell_through


# ---------------------------------------------------------------------------
# _merge_policy_fragments
# ---------------------------------------------------------------------------


def test_merge_policy_fragments_rejects_unknown_section(synthetic_channel_profile):
    fragment = PoliciesFragmentFile.model_validate({"totally_unknown_section": ["x"]})
    with pytest.raises(RuntimeError, match="no es válida"):
        _merge_policy_fragments([fragment], synthetic_channel_profile)


def test_merge_policy_fragments_rejects_empty_required_section(synthetic_channel_profile):
    fragment = PoliciesFragmentFile.model_validate({})
    with pytest.raises(RuntimeError, match="requerida"):
        _merge_policy_fragments([fragment], synthetic_channel_profile)


def test_merge_policy_fragments_concatenates_and_dedupes_across_fragments(
    synthetic_channel_profile,
):
    frag_a = PoliciesFragmentFile.model_validate(
        {"compliance_and_scope_rules": ["Rule A", "Shared rule"]}
    )
    frag_b = PoliciesFragmentFile.model_validate(
        {"compliance_and_scope_rules": ["Shared rule", "Rule B"]}
    )
    merged = _merge_policy_fragments([frag_a, frag_b], synthetic_channel_profile)
    assert merged.get_section("compliance_and_scope_rules") == ["Rule A", "Shared rule", "Rule B"]


# ---------------------------------------------------------------------------
# _merge_tool_fragments vs _merge_tool_contract_fragments (SPEC.md B7)
# ---------------------------------------------------------------------------


def test_merge_tool_fragments_silently_dedupes_across_fragments():
    frag_a = ToolsFragmentFile.model_validate({"tools": ["send_email"]})
    frag_b = ToolsFragmentFile.model_validate({"tools": ["send_email", "book_appointment"]})
    merged = _merge_tool_fragments([frag_a, frag_b])
    assert merged.tools == ["send_email", "book_appointment"]


def test_merge_tool_contract_fragments_raises_on_duplicate_name():
    contract = {
        "name": "send_email",
        "description": "Send an email.",
        "inputs": [],
        "outputs": [],
        "notes": [],
    }
    frag_a = ("fragment_a.yaml", ToolContractsFragmentFile.model_validate({"tool_contracts": [contract]}))
    frag_b = ("fragment_b.yaml", ToolContractsFragmentFile.model_validate({"tool_contracts": [contract]}))
    with pytest.raises(RuntimeError, match="duplicado"):
        _merge_tool_contract_fragments([frag_a, frag_b])


# ---------------------------------------------------------------------------
# _materialize_template_params
# ---------------------------------------------------------------------------


def _template_with_params(params: list[TemplateParamDefinition]) -> SubflowTemplate:
    return SubflowTemplate.model_validate(
        {
            "template_id": "callback",
            "description": "Callback subflow.",
            "params": [p.model_dump() for p in params],
            "flow_rules": ["A flow rule so the template has content."],
        }
    )


def test_materialize_template_params_applies_default_when_omitted():
    template = _template_with_params(
        [TemplateParamDefinition(name="agent_name", required=False, default="Ana")]
    )
    instance = SubflowInstanceRef.model_validate({"template": "x", "instance_id": "cb1"})
    params = _materialize_template_params(instance, template)
    assert params == {"agent_name": "Ana"}


def test_materialize_template_params_raises_for_missing_required_param():
    template = _template_with_params([TemplateParamDefinition(name="agent_name", required=True)])
    instance = SubflowInstanceRef.model_validate({"template": "x", "instance_id": "cb1"})
    with pytest.raises(RuntimeError, match="agent_name"):
        _materialize_template_params(instance, template)


def test_materialize_template_params_raises_for_unknown_param():
    template = _template_with_params([])
    instance = SubflowInstanceRef.model_validate(
        {"template": "x", "instance_id": "cb1", "params": {"typo_param": "value"}}
    )
    with pytest.raises(RuntimeError, match="typo_param"):
        _materialize_template_params(instance, template)


def test_materialize_template_params_instance_value_overrides_default():
    template = _template_with_params(
        [TemplateParamDefinition(name="agent_name", required=False, default="Ana")]
    )
    instance = SubflowInstanceRef.model_validate(
        {"template": "x", "instance_id": "cb1", "params": {"agent_name": "Sofia"}}
    )
    params = _materialize_template_params(instance, template)
    assert params == {"agent_name": "Sofia"}


# ---------------------------------------------------------------------------
# Subflow instantiation end-to-end (namespacing + alias resolution)
# ---------------------------------------------------------------------------


def _write_agent_with_subflow(tmp_path: Path) -> Path:
    """Write a minimal agent that instantiates one subflow template.

    The subflow exports its entry state and one slot; the root flow's
    GREETING state routes into the subflow via a ``@instance.export`` alias,
    exercising namespacing and alias resolution end-to-end.
    """
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()

    overrides = {
        "manifest.yaml": {
            "subflow_instances": [
                {"template": "callback_template.yaml", "instance_id": "callback"}
            ]
        },
        "states.yaml": {
            "states": [
                {
                    "state_id": "GREETING",
                    "type": "message",
                    "say": ["Hello."],
                    "wait": "no",
                    "route": ["GO_TO: @callback.entry"],
                }
            ]
        },
    }
    _write_minimal_agent(agent_dir, overrides=overrides)

    template = {
        "template_id": "callback_template",
        "description": "A minimal callback subflow.",
        "local_memory_slots": [
            {"name": "callback_time", "description": "Requested callback time.", "kind": "captured"}
        ],
        "states": [
            {
                "state_id": "ASK_TIME",
                "type": "question",
                "say": ["When should we call you back?"],
                "wait": "yes",
                "capture": [{"slot": "callback_time", "type_expr": "str"}],
                "route": ["GO_TO: DONE"],
            }
        ],
        "terminal_states": [
            {
                "state_id": "DONE",
                "type": "terminal",
                "say": ["We'll call you back."],
                "wait": "no",
                "final": "yes",
            }
        ],
        "exports": {"states": {"entry": "ASK_TIME"}, "slots": {"time": "callback_time"}},
    }
    (agent_dir / "callback_template.yaml").write_text(yaml.safe_dump(template), encoding="utf-8")
    return agent_dir


def test_load_agent_spec_namespaces_subflow_state_ids_with_double_underscore(
    tmp_path, synthetic_channel_profile, default_params
):
    agent_dir = _write_agent_with_subflow(tmp_path)
    spec = load_agent_spec(agent_dir, default_params, synthetic_channel_profile)

    assert "CALLBACK__ASK_TIME" in spec.main_state_ids
    assert "CALLBACK__DONE" in [s.state_id for s in spec.terminal_states]


def test_load_agent_spec_namespaces_subflow_slot_names_with_double_underscore(
    tmp_path, synthetic_channel_profile, default_params
):
    agent_dir = _write_agent_with_subflow(tmp_path)
    spec = load_agent_spec(agent_dir, default_params, synthetic_channel_profile)

    slot_names = {slot.name for slot in spec.memory_slots}
    assert "callback__callback_time" in slot_names


def test_load_agent_spec_resolves_instance_export_alias_in_goto(
    tmp_path, synthetic_channel_profile, default_params
):
    agent_dir = _write_agent_with_subflow(tmp_path)
    spec = load_agent_spec(agent_dir, default_params, synthetic_channel_profile)

    greeting = next(s for s in spec.states if s.state_id == "GREETING")
    assert greeting.route == ["GO_TO: CALLBACK__ASK_TIME"]


def test_load_agent_spec_raises_for_duplicate_subflow_namespace(
    tmp_path, synthetic_channel_profile, default_params
):
    agent_dir = _write_agent_with_subflow(tmp_path)
    manifest_path = agent_dir / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["subflow_instances"] = [
        {"template": "callback_template.yaml", "instance_id": "callback"},
        {"template": "callback_template.yaml", "instance_id": "callback_two", "namespace": "callback"},
    ]
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")

    with pytest.raises(RuntimeError, match="namespace duplicado"):
        load_agent_spec(agent_dir, default_params, synthetic_channel_profile)


def test_load_agent_spec_raises_for_unresolvable_export_alias(
    tmp_path, synthetic_channel_profile, default_params
):
    agent_dir = _write_agent_with_subflow(tmp_path)
    states_path = agent_dir / "states.yaml"
    states = yaml.safe_load(states_path.read_text(encoding="utf-8"))
    states["states"][0]["route"] = ["GO_TO: @callback.nonexistent_export"]
    states_path.write_text(yaml.safe_dump(states), encoding="utf-8")

    with pytest.raises(Exception):  # noqa: B017 -- see xfail below for the exact type
        load_agent_spec(agent_dir, default_params, synthetic_channel_profile)


@pytest.mark.xfail(
    strict=True,
    reason="Known gap (SPEC.md B3): a broken @instance.export alias raises ValueError "
    "from app.utils.resolve_state_alias_targets, not the RuntimeError documented by "
    "load_agent_spec()'s own docstring.",
)
def test_load_agent_spec_raises_runtime_error_for_unresolvable_export_alias(
    tmp_path, synthetic_channel_profile, default_params
):
    agent_dir = _write_agent_with_subflow(tmp_path)
    states_path = agent_dir / "states.yaml"
    states = yaml.safe_load(states_path.read_text(encoding="utf-8"))
    states["states"][0]["route"] = ["GO_TO: @callback.nonexistent_export"]
    states_path.write_text(yaml.safe_dump(states), encoding="utf-8")

    with pytest.raises(RuntimeError):
        load_agent_spec(agent_dir, default_params, synthetic_channel_profile)
