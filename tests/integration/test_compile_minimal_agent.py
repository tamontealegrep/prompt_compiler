"""Integration test: compile the on-disk minimal fixture agent end-to-end.

Uses the public ``compile_agent()`` entry point exactly as ``build_prompt.py``
and ``main.py`` do — including the real ``profiles/channels/voice.yaml`` and
(optionally) ``profiles/compliance/medical_es.yaml``. The fixture at
``tests/fixtures/minimal_agent/`` is written to satisfy the real voice
profile's required policy sections; if that profile changes what it
requires, this fixture needs a matching update (see TESTING.md §2).
"""

from __future__ import annotations

from app.compiler import compile_agent
from app.schemas import ChannelType, CompilationParams
from tests.conftest import MINIMAL_AGENT_DIR


def test_compile_minimal_agent_has_no_validation_errors():
    outputs = compile_agent(MINIMAL_AGENT_DIR, CompilationParams(channel=ChannelType.VOICE))
    assert outputs.validation_report.errors == []


def test_compile_minimal_agent_produces_a_non_empty_system_prompt():
    outputs = compile_agent(MINIMAL_AGENT_DIR, CompilationParams(channel=ChannelType.VOICE))
    assert "GREETING" in outputs.system_prompt
    assert "CLOSE" in outputs.system_prompt


def test_compile_minimal_agent_produces_reference_asset_markdown_and_json():
    outputs = compile_agent(MINIMAL_AGENT_DIR, CompilationParams(channel=ChannelType.VOICE))
    assert outputs.reference_asset_markdown is not None
    assert outputs.reference_asset_json is not None
    assert outputs.reference_asset_json["agent_id"] == "minimal_agent"


def test_compile_minimal_agent_deduplication_report_is_clean():
    outputs = compile_agent(MINIMAL_AGENT_DIR, CompilationParams(channel=ChannelType.VOICE))
    assert outputs.deduplication_report.has_duplicates() is False


def test_compile_minimal_agent_orphan_report_shows_zero_unreachable_states():
    outputs = compile_agent(MINIMAL_AGENT_DIR, CompilationParams(channel=ChannelType.VOICE))
    assert "Unreachable states: 0" in outputs.orphan_report


def test_compile_minimal_agent_stats_reflect_the_two_node_flow():
    outputs = compile_agent(MINIMAL_AGENT_DIR, CompilationParams(channel=ChannelType.VOICE))
    assert outputs.stats.total_states == 1
    assert outputs.stats.total_subflows_instantiated == 0


def test_compile_minimal_agent_is_deterministic_across_two_runs():
    params = CompilationParams(channel=ChannelType.VOICE)
    first = compile_agent(MINIMAL_AGENT_DIR, params)
    second = compile_agent(MINIMAL_AGENT_DIR, params)
    assert first.system_prompt == second.system_prompt
    assert first.reference_asset_markdown == second.reference_asset_markdown
    assert first.reference_asset_json == second.reference_asset_json
    assert first.split_system_prompt == second.split_system_prompt
    assert first.split_knowledge_base == second.split_knowledge_base
    assert first.split_system_prompt_mini == second.split_system_prompt_mini
    assert first.split_knowledge_base_mini == second.split_knowledge_base_mini


def test_compile_minimal_agent_with_compliance_profile_flags_the_missing_disclaimer():
    outputs = compile_agent(
        MINIMAL_AGENT_DIR,
        CompilationParams(channel=ChannelType.VOICE, compliance_profile="medical_es"),
    )
    codes = {issue.code for issue in outputs.validation_report.warnings}
    assert "COMPLIANCE_VIOLATION" in codes


def test_compile_minimal_agent_with_compliance_profile_has_no_diagnostic_promise_errors():
    # The fixture's SAY content is clean; MED_001/MED_003 (error-level) must not fire.
    outputs = compile_agent(
        MINIMAL_AGENT_DIR,
        CompilationParams(channel=ChannelType.VOICE, compliance_profile="medical_es"),
    )
    assert outputs.validation_report.errors == []


def test_compile_agent_never_writes_to_disk(tmp_path, monkeypatch):
    """compile_agent() must be pure — no file is created anywhere during a compile."""
    before = set(tmp_path.rglob("*"))
    compile_agent(MINIMAL_AGENT_DIR, CompilationParams(channel=ChannelType.VOICE))
    after = set(tmp_path.rglob("*"))
    assert before == after


def test_compile_minimal_agent_produces_a_mini_system_prompt():
    outputs = compile_agent(MINIMAL_AGENT_DIR, CompilationParams(channel=ChannelType.VOICE))
    assert outputs.system_prompt_mini is not None
    assert "MSG GREETING" in outputs.system_prompt_mini
    assert "END CLOSE" in outputs.system_prompt_mini


def test_compile_minimal_agent_mini_prompt_has_no_validation_impact():
    """Rendering the mini prompt alongside the full one must not change validation."""
    outputs = compile_agent(MINIMAL_AGENT_DIR, CompilationParams(channel=ChannelType.VOICE))
    assert outputs.validation_report.errors == []


def test_compile_minimal_agent_mini_prompt_is_deterministic_across_two_runs():
    params = CompilationParams(channel=ChannelType.VOICE)
    first = compile_agent(MINIMAL_AGENT_DIR, params)
    second = compile_agent(MINIMAL_AGENT_DIR, params)
    assert first.system_prompt_mini == second.system_prompt_mini


def test_compile_minimal_agent_mini_prompt_never_shows_wait_or_final_labels():
    """B2 made WAIT/FINAL fully derivable from TYPE; the mini renderer must never restate
    them on a node. (COMPACT_OBJECT_NOTATION's teaching prose legitimately says the words
    "WAIT"/"FINAL" once each, explaining the rule — this checks the rendered STATES/
    TERMINAL_STATES section specifically, not the whole document.)"""
    outputs = compile_agent(MINIMAL_AGENT_DIR, CompilationParams(channel=ChannelType.VOICE))
    states_section = outputs.system_prompt_mini.split("## STATES", 1)[1]
    assert "WAIT" not in states_section
    assert "FINAL" not in states_section
