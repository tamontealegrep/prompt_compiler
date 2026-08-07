"""Integration test: compile an agent that instantiates a subflow template.

Exercises the full pipeline's namespacing and alias-resolution behavior
through the public ``compile_agent()`` entry point, using a synthetic
on-disk agent (built fresh per test via ``tmp_path`` so it doesn't need to
live under ``tests/fixtures/``) that satisfies the real voice channel
profile's required policy sections.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.compiler import compile_agent
from app.schemas import ChannelType, CompilationParams

REQUIRED_VOICE_POLICY_SECTIONS = {
    "name_handling_rules": ["Use the caller's first name only."],
    "style_and_vui_rules": ["Keep turns under two sentences."],
    "pronunciation_rules": ["Spell out acronyms on first use."],
    "compliance_and_scope_rules": ["Never give medical or legal advice."],
    "data_and_variable_rules": ["Never speak a raw slot or constant aloud."],
}


def _write_agent_with_subflow(base: Path) -> Path:
    agent_dir = base / "agent_with_subflow"
    agent_dir.mkdir()

    (agent_dir / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "agent_id": "subflow_agent",
                "language": "es",
                "start_at": "GREETING",
                "includes": {
                    "states": ["states.yaml"],
                    "terminal_states": ["terminal_states.yaml"],
                },
                "subflow_instances": [
                    {"template": "callback_template.yaml", "instance_id": "callback"}
                ],
            }
        ),
        encoding="utf-8",
    )
    (agent_dir / "constants.yaml").write_text(
        yaml.safe_dump(
            {"constants": [{"name": "MAX_RETRY", "description": "Max retries.", "value": "3"}]}
        ),
        encoding="utf-8",
    )
    (agent_dir / "identity.yaml").write_text(
        yaml.safe_dump({"identity": ["A synthetic test agent with a subflow."]}), encoding="utf-8"
    )
    (agent_dir / "objectives.yaml").write_text(
        yaml.safe_dump(
            {
                "primary_objective": ["Greet, schedule a callback, and close."],
                "secondary_objectives": ["Demonstrate subflow instantiation end-to-end."],
                "success_alternatives": ["The caller books a callback time."],
            }
        ),
        encoding="utf-8",
    )
    (agent_dir / "context.yaml").write_text(
        yaml.safe_dump(
            {
                "company_context": ["Synthetic company."],
                "approved_services": ["Synthetic callback service."],
                "summary_services_library": [
                    {
                        "key": "CALLBACK_SERVICE",
                        "procedure": "callback_service",
                        "text": "Synthetic callback scheduling.",
                    }
                ],
                "approved_process_intro": "Synthetic process.",
                "approved_process_steps": [{"title": "Step", "text": "Schedule callback."}],
                "support_and_trust": ["This is a test fixture."],
            }
        ),
        encoding="utf-8",
    )
    (agent_dir / "input_variables.yaml").write_text(
        yaml.safe_dump({"input_variables": [{"name": "caller_name", "description": "Name."}]}),
        encoding="utf-8",
    )
    (agent_dir / "policies.yaml").write_text(
        yaml.safe_dump(REQUIRED_VOICE_POLICY_SECTIONS), encoding="utf-8"
    )
    (agent_dir / "tools.yaml").write_text(yaml.safe_dump({"tools": ["noop_tool"]}), encoding="utf-8")
    (agent_dir / "tool_contracts.yaml").write_text(
        yaml.safe_dump(
            {
                "tool_contracts": [
                    {
                        "name": "noop_tool",
                        "description": "No-op tool for schema satisfaction.",
                        "inputs": [],
                        "outputs": [],
                        "notes": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (agent_dir / "memory_slots.yaml").write_text(
        yaml.safe_dump(
            {
                "memory_slots": [
                    {"name": "current_state", "description": "Active state.", "kind": "dynamic_state"},
                    {"name": "resume_state", "description": "Resume state.", "kind": "dynamic_state"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (agent_dir / "states.yaml").write_text(
        yaml.safe_dump(
            {
                "states": [
                    {
                        "state_id": "GREETING",
                        "type": "message",
                        "say": ["Hello, let's schedule a callback."],
                        "wait": "no",
                        "route": ["GO_TO: @callback.entry"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (agent_dir / "terminal_states.yaml").write_text(
        yaml.safe_dump({"terminal_states": []}), encoding="utf-8"
    )
    # terminal_states requires non-empty content when the file is loaded via an
    # include — declare no include instead and rely on the subflow's own
    # terminal state as the flow's only terminal.
    manifest = yaml.safe_load((agent_dir / "manifest.yaml").read_text(encoding="utf-8"))
    manifest["includes"]["terminal_states"] = []
    (agent_dir / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")

    (agent_dir / "callback_template.yaml").write_text(
        yaml.safe_dump(
            {
                "template_id": "callback_template",
                "description": "A minimal callback-scheduling subflow.",
                "local_memory_slots": [
                    {
                        "name": "callback_time",
                        "description": "Requested callback time.",
                        "kind": "captured",
                    }
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
                        "say": ["We'll call you back at the requested time."],
                        "wait": "no",
                        "final": "yes",
                    }
                ],
                "exports": {"states": {"entry": "ASK_TIME"}, "slots": {"time": "callback_time"}},
            }
        ),
        encoding="utf-8",
    )
    return agent_dir


@pytest.fixture
def subflow_agent_dir(tmp_path) -> Path:
    return _write_agent_with_subflow(tmp_path)


def test_compile_agent_with_subflow_has_no_validation_errors(subflow_agent_dir):
    outputs = compile_agent(subflow_agent_dir, CompilationParams(channel=ChannelType.VOICE))
    assert outputs.validation_report.errors == []


def test_compile_agent_with_subflow_namespaces_states_in_the_prompt(subflow_agent_dir):
    outputs = compile_agent(subflow_agent_dir, CompilationParams(channel=ChannelType.VOICE))
    assert "CALLBACK__ASK_TIME" in outputs.system_prompt
    assert "CALLBACK__DONE" in outputs.system_prompt


def test_compile_agent_with_subflow_resolves_export_alias_in_root_route(subflow_agent_dir):
    outputs = compile_agent(subflow_agent_dir, CompilationParams(channel=ChannelType.VOICE))
    assert "GO_TO: CALLBACK__ASK_TIME" in outputs.system_prompt
    assert "@callback.entry" not in outputs.system_prompt


def test_compile_agent_with_subflow_reports_one_instantiated_subflow(subflow_agent_dir):
    outputs = compile_agent(subflow_agent_dir, CompilationParams(channel=ChannelType.VOICE))
    assert outputs.stats.total_subflows_instantiated == 1


def test_compile_agent_with_subflow_embed_subflows_false_produces_separate_document(
    subflow_agent_dir,
):
    outputs = compile_agent(
        subflow_agent_dir,
        CompilationParams(channel=ChannelType.VOICE, embed_subflows=False),
    )
    assert "CALLBACK" in outputs.subflow_documents
    assert "CALLBACK__ASK_TIME" in outputs.subflow_documents["CALLBACK"]
    # The AVAILABLE_SUBFLOWS navigation index legitimately names the entry
    # state id; what must be absent from the main prompt is the state's own
    # rendered block.
    assert "### STATE CALLBACK__ASK_TIME" not in outputs.system_prompt
