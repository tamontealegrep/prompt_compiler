"""Unit tests for app/validators.py — the 11 graph validators + 4 compliance checkers."""

from __future__ import annotations

import pytest

from app.schemas import (
    ComplianceProfile,
    ComplianceRuleDefinition,
    ComplianceSeverity,
    ConstantItem,
    FAQModel,
    InputVariable,
    MemorySlot,
    ToolContract,
)
from app.validators import build_orphan_state_report, validate_agent_spec
from tests.conftest import (
    build_message_state,
    build_minimal_agent_spec,
    build_minimal_manifest,
)


def _codes(issues) -> set[str]:
    return {issue.code for issue in issues}


# ---------------------------------------------------------------------------
# Baseline: the minimal spec has no errors
# ---------------------------------------------------------------------------


def test_minimal_agent_spec_has_no_errors():
    spec = build_minimal_agent_spec()
    report = validate_agent_spec(spec)
    assert report.errors == []


# ---------------------------------------------------------------------------
# 1. _validate_duplicates
# ---------------------------------------------------------------------------


def test_duplicate_constant_names_are_reported():
    spec = build_minimal_agent_spec(
        constants=[
            ConstantItem(name="MAX_RETRY", description="d1", value="3"),
            ConstantItem(name="MAX_RETRY", description="d2", value="5"),
        ]
    )
    report = validate_agent_spec(spec)
    assert "DUPLICATE_CONSTANT" in _codes(report.errors)


def test_duplicate_state_id_across_states_and_terminal_states_is_reported():
    spec = build_minimal_agent_spec(
        states=[build_message_state("CLOSE")],  # collides with the default terminal id
    )
    report = validate_agent_spec(spec)
    assert "DUPLICATE_OBJECT_ID" in _codes(report.errors)


# ---------------------------------------------------------------------------
# 2. _validate_start_at
# ---------------------------------------------------------------------------


def test_start_at_pointing_to_nonexistent_state_is_reported():
    spec = build_minimal_agent_spec(manifest=build_minimal_manifest(start_at="NOWHERE"))
    report = validate_agent_spec(spec)
    assert "INVALID_START_AT" in _codes(report.errors)


def test_start_at_pointing_to_terminal_state_is_reported():
    # start_at must reference a *main* state, not a terminal one.
    spec = build_minimal_agent_spec(manifest=build_minimal_manifest(start_at="CLOSE"))
    report = validate_agent_spec(spec)
    assert "INVALID_START_AT" in _codes(report.errors)


# ---------------------------------------------------------------------------
# 3. _validate_faq_resume_targets
# ---------------------------------------------------------------------------


def test_faq_resume_to_pointing_to_nonexistent_state_is_reported():
    state = build_message_state(faq_resume_to="NOWHERE")
    spec = build_minimal_agent_spec(states=[state])
    report = validate_agent_spec(spec)
    assert "INVALID_FAQ_RESUME_TO" in _codes(report.errors)


def test_faq_resume_to_without_faq_policy_warns():
    state = build_message_state(faq_resume_to="GREETING")
    spec = build_minimal_agent_spec(states=[state], faq_policy=[])
    report = validate_agent_spec(spec)
    assert "FAQ_RESUME_WITHOUT_POLICY" in _codes(report.warnings)


def test_faq_policy_without_valid_entrypoint_is_reported():
    spec = build_minimal_agent_spec(faq_policy=["Some unrelated policy line with no GO_TO."])
    report = validate_agent_spec(spec)
    assert "MISSING_FAQ_ENTRYPOINT" in _codes(report.errors)


# ---------------------------------------------------------------------------
# 4. _validate_goto_targets
# ---------------------------------------------------------------------------


def test_goto_to_unknown_static_target_is_reported():
    state = build_message_state(route=["GO_TO: DOES_NOT_EXIST"])
    spec = build_minimal_agent_spec(states=[state])
    report = validate_agent_spec(spec)
    assert "UNKNOWN_GOTO_TARGET" in _codes(report.errors)


def test_goto_to_undeclared_dynamic_slot_is_reported():
    state = build_message_state(route=["GO_TO: [not_a_declared_dynamic_slot]"])
    spec = build_minimal_agent_spec(states=[state])
    report = validate_agent_spec(spec)
    assert "INVALID_DYNAMIC_GOTO_SLOT" in _codes(report.errors)


def test_goto_to_declared_dynamic_slot_is_valid():
    state = build_message_state(route=["GO_TO: [current_state]"])
    spec = build_minimal_agent_spec(states=[state])
    report = validate_agent_spec(spec)
    assert "INVALID_DYNAMIC_GOTO_SLOT" not in _codes(report.errors)


def test_line_with_goto_prefix_but_unparseable_target_is_reported():
    # "GO_TO:" is present but followed by nothing the regex can capture.
    state = build_message_state(route=["GO_TO: "])
    spec = build_minimal_agent_spec(states=[state])
    report = validate_agent_spec(spec)
    assert "UNPARSEABLE_GOTO" in _codes(report.errors)


@pytest.mark.xfail(
    strict=True,
    reason="Known gap (SPEC.md B4): a case-typo'd 'go_to:' is invisible to both "
    "extraction and the UNPARSEABLE_GOTO check (both use the exact-case substring "
    "'GO_TO:'), so it's silently treated as if there were no target at all.",
)
def test_line_with_lowercase_goto_is_reported_as_unparseable():
    state = build_message_state(route=["go_to: CLOSE"])
    spec = build_minimal_agent_spec(states=[state])
    report = validate_agent_spec(spec)
    assert "UNPARSEABLE_GOTO" in _codes(report.errors)


# ---------------------------------------------------------------------------
# 5. _validate_tools_and_contracts
# ---------------------------------------------------------------------------


def test_execute_referencing_undeclared_tool_is_reported():
    state = build_message_state(
        state_id="BOOK", type="action", say=[], execute="undeclared_tool", route=["GO_TO: CLOSE"]
    )
    spec = build_minimal_agent_spec(states=[state])
    report = validate_agent_spec(spec)
    assert "UNDECLARED_TOOL" in _codes(report.errors)


def test_execute_referencing_tool_without_contract_is_reported():
    state = build_message_state(
        state_id="BOOK", type="action", say=[], execute="book_appointment", route=["GO_TO: CLOSE"]
    )
    spec = build_minimal_agent_spec(states=[state], tools=["book_appointment"])
    report = validate_agent_spec(spec)
    assert "MISSING_TOOL_CONTRACT" in _codes(report.errors)


def test_unused_declared_tool_warns():
    spec = build_minimal_agent_spec(
        tools=["unused_tool"],
        tool_contracts=[
            ToolContract.model_validate(
                {"name": "unused_tool", "description": "d", "inputs": [], "outputs": []}
            )
        ],
    )
    report = validate_agent_spec(spec)
    assert "UNUSED_TOOL" in _codes(report.warnings)


def test_action_capture_not_in_contract_outputs_warns():
    contract = ToolContract.model_validate(
        {
            "name": "book_appointment",
            "description": "d",
            "inputs": [],
            "outputs": [{"name": "confirmation_id", "description": "id"}],
        }
    )
    state = build_message_state(
        state_id="BOOK",
        type="action",
        say=[],
        execute="book_appointment",
        capture=[{"slot": "unrelated_slot", "type_expr": "str"}],
        route=["GO_TO: CLOSE"],
    )
    spec = build_minimal_agent_spec(
        states=[state],
        tools=["book_appointment"],
        tool_contracts=[contract],
        memory_slots=[
            MemorySlot(name="current_state", description="d", kind="dynamic_state"),
            MemorySlot(name="resume_state", description="d", kind="dynamic_state"),
            MemorySlot(name="unrelated_slot", description="d", kind="captured"),
        ],
    )
    report = validate_agent_spec(spec)
    assert "CAPTURE_NOT_IN_CONTRACT_OUTPUTS" in _codes(report.warnings)


# ---------------------------------------------------------------------------
# 6. _validate_placeholders_and_memory_slots
# ---------------------------------------------------------------------------


def test_dynamic_state_slot_not_declared_is_reported():
    spec = build_minimal_agent_spec(memory_slots=[])
    report = validate_agent_spec(spec)
    assert "DYNAMIC_SLOT_NOT_DECLARED" in _codes(report.errors)


def test_undeclared_runtime_variable_reference_is_reported():
    state = build_message_state(say=["Hello {{undeclared_var}}."])
    spec = build_minimal_agent_spec(states=[state])
    report = validate_agent_spec(spec)
    assert "UNDECLARED_RUNTIME_VARIABLE" in _codes(report.errors)


def test_undeclared_constant_reference_is_reported():
    state = build_message_state(say=["Use <UNDECLARED_CONST> here."])
    spec = build_minimal_agent_spec(states=[state])
    report = validate_agent_spec(spec)
    assert "UNDECLARED_CONSTANT" in _codes(report.errors)


def test_undeclared_memory_slot_reference_is_reported():
    state = build_message_state(do=["Check [undeclared_slot] before continuing."])
    spec = build_minimal_agent_spec(states=[state])
    report = validate_agent_spec(spec)
    assert "UNDECLARED_MEMORY_SLOT" in _codes(report.errors)


def test_capture_slot_not_declared_is_reported():
    state = build_message_state(
        type="question",
        wait="yes",
        say=["What is your name?"],
        capture=[{"slot": "not_declared", "type_expr": "str"}],
    )
    spec = build_minimal_agent_spec(states=[state])
    report = validate_agent_spec(spec)
    assert "CAPTURE_SLOT_NOT_DECLARED" in _codes(report.errors)


def test_unused_memory_slot_warns():
    spec = build_minimal_agent_spec(
        memory_slots=[
            MemorySlot(name="current_state", description="d", kind="dynamic_state"),
            MemorySlot(name="resume_state", description="d", kind="dynamic_state"),
            MemorySlot(name="never_used", description="d", kind="other"),
        ]
    )
    report = validate_agent_spec(spec)
    assert "UNUSED_MEMORY_SLOT" in _codes(report.warnings)


# ---------------------------------------------------------------------------
# 7. _validate_summary_coverage
# ---------------------------------------------------------------------------


def test_missing_service_summary_for_declared_procedure_is_reported():
    spec = build_minimal_agent_spec(
        input_variables=[
            InputVariable(
                name="procedure", description="d", allowed_values=["ivf", "egg_donation"]
            )
        ],
    )
    report = validate_agent_spec(spec)
    assert "MISSING_SERVICE_SUMMARIES" in _codes(report.errors)


def test_summary_coverage_is_skipped_without_a_procedure_input_variable():
    spec = build_minimal_agent_spec()  # default fixture has no "procedure" input variable
    report = validate_agent_spec(spec)
    assert "MISSING_SERVICE_SUMMARIES" not in _codes(report.errors)


# ---------------------------------------------------------------------------
# 8. _validate_faq_match_collisions
# ---------------------------------------------------------------------------


def test_faq_match_phrase_collision_across_faqs_warns():
    faq_a = FAQModel.model_validate(
        {"faq_id": "FAQ_A", "type": "message", "match": ["how much does it cost"], "say": ["Answer A"]}
    )
    faq_b = FAQModel.model_validate(
        {"faq_id": "FAQ_B", "type": "message", "match": ["How Much Does It Cost"], "say": ["Answer B"]}
    )
    spec = build_minimal_agent_spec(faqs=[faq_a, faq_b])
    report = validate_agent_spec(spec)
    assert "FAQ_MATCH_COLLISION" in _codes(report.warnings)


# ---------------------------------------------------------------------------
# 9. _validate_reachability_and_cycles
# ---------------------------------------------------------------------------


def test_unreachable_state_is_reported():
    orphan = build_message_state(state_id="ORPHAN")
    spec = build_minimal_agent_spec(states=[build_message_state(), orphan])
    report = validate_agent_spec(spec)
    assert "UNREACHABLE_STATE" in _codes(report.errors)
    assert any(i.location == "ORPHAN" for i in report.errors if i.code == "UNREACHABLE_STATE")


def test_no_terminal_states_is_reported():
    spec = build_minimal_agent_spec(terminal_states=[])
    report = validate_agent_spec(spec)
    assert "NO_TERMINAL_STATES" in _codes(report.errors)


def test_suspicious_cycle_without_terminal_exit_warns():
    a = build_message_state("A", route=["GO_TO: B"])
    b = build_message_state("B", route=["GO_TO: A"])
    spec = build_minimal_agent_spec(
        states=[a, b], manifest=build_minimal_manifest(start_at="A")
    )
    report = validate_agent_spec(spec)
    assert "SUSPICIOUS_CYCLE" in _codes(report.warnings)


def test_cycle_with_static_exit_to_terminal_does_not_warn():
    a = build_message_state("A", route=["GO_TO: B"])
    b = build_message_state("B", route=["GO_TO: A", "GO_TO: CLOSE"])
    spec = build_minimal_agent_spec(
        states=[a, b], manifest=build_minimal_manifest(start_at="A")
    )
    report = validate_agent_spec(spec)
    assert "SUSPICIOUS_CYCLE" not in _codes(report.warnings)


# ---------------------------------------------------------------------------
# 10. _validate_redundant_route_fallback
# ---------------------------------------------------------------------------


def test_redundant_route_and_fallback_to_same_target_warns():
    state = build_message_state(route=["GO_TO: CLOSE"], fallback=["GO_TO: CLOSE"])
    spec = build_minimal_agent_spec(states=[state])
    report = validate_agent_spec(spec)
    assert "REDUNDANT_FALLBACK" in _codes(report.warnings)


def test_different_route_and_fallback_targets_do_not_warn():
    other = build_message_state("OTHER", route=["GO_TO: CLOSE"])
    state = build_message_state(route=["GO_TO: OTHER"], fallback=["GO_TO: CLOSE"])
    spec = build_minimal_agent_spec(states=[state, other])
    report = validate_agent_spec(spec)
    assert "REDUNDANT_FALLBACK" not in _codes(report.warnings)


# ---------------------------------------------------------------------------
# 11. _validate_question_self_loops
# ---------------------------------------------------------------------------


def test_question_self_loop_without_retry_counter_warns():
    state = build_message_state(
        type="question",
        wait="yes",
        say=["What is your name?"],
        route=["GO_TO: GREETING"],
    )
    spec = build_minimal_agent_spec(states=[state])
    report = validate_agent_spec(spec)
    assert "QUESTION_SELF_LOOP_WITHOUT_RETRY_COUNTER" in _codes(report.warnings)


def test_question_self_loop_with_retry_counter_does_not_warn():
    state = build_message_state(
        type="question",
        wait="yes",
        say=["What is your name?"],
        do=["Check [name_retry_count] before asking again."],
        route=["GO_TO: GREETING"],
    )
    spec = build_minimal_agent_spec(states=[state])
    report = validate_agent_spec(spec)
    assert "QUESTION_SELF_LOOP_WITHOUT_RETRY_COUNTER" not in _codes(report.warnings)


# ---------------------------------------------------------------------------
# Compliance checkers
# ---------------------------------------------------------------------------


def _compliance_profile(
    check: str, severity: ComplianceSeverity = ComplianceSeverity.ERROR
) -> ComplianceProfile:
    return ComplianceProfile(
        profile_id="test_profile",
        display_name="Test Profile",
        rules=[
            ComplianceRuleDefinition(
                rule_id="TEST_001", description="A test rule.", severity=severity, check=check
            )
        ],
    )


def test_no_diagnostic_promises_flags_matching_say_content():
    state = build_message_state(say=["Te garantizamos un embarazo exitoso."])
    spec = build_minimal_agent_spec(states=[state])
    report = validate_agent_spec(spec, _compliance_profile("no_diagnostic_promises"))
    assert "COMPLIANCE_VIOLATION" in _codes(report.errors)


def test_no_diagnostic_promises_passes_clean_say_content():
    spec = build_minimal_agent_spec()
    report = validate_agent_spec(spec, _compliance_profile("no_diagnostic_promises"))
    assert "COMPLIANCE_VIOLATION" not in _codes(report.errors)


def test_no_price_guarantees_flags_matching_say_content():
    state = build_message_state(say=["El precio está garantizado sin cambios."])
    spec = build_minimal_agent_spec(states=[state])
    report = validate_agent_spec(spec, _compliance_profile("no_price_guarantees"))
    assert "COMPLIANCE_VIOLATION" in _codes(report.errors)


def test_scope_rules_required_flags_empty_section():
    spec = build_minimal_agent_spec()  # default policies has no sections at all
    report = validate_agent_spec(spec, _compliance_profile("scope_rules_required"))
    assert "COMPLIANCE_VIOLATION" in _codes(report.errors)


def test_scope_rules_required_passes_when_section_is_populated():
    from tests.conftest import build_minimal_policies

    spec = build_minimal_agent_spec(
        policies=build_minimal_policies(compliance_and_scope_rules=["Never give medical advice."])
    )
    report = validate_agent_spec(spec, _compliance_profile("scope_rules_required"))
    assert "COMPLIANCE_VIOLATION" not in _codes(report.errors)


def test_requires_disclaimer_node_flags_when_no_node_mentions_keywords():
    spec = build_minimal_agent_spec()
    report = validate_agent_spec(spec, _compliance_profile("requires_disclaimer_node"))
    assert "COMPLIANCE_VIOLATION" in _codes(report.errors)


def test_requires_disclaimer_node_passes_when_a_node_mentions_keywords():
    state = build_message_state(goal=["Explain our privacidad policy to the caller."])
    spec = build_minimal_agent_spec(states=[state])
    report = validate_agent_spec(spec, _compliance_profile("requires_disclaimer_node"))
    assert "COMPLIANCE_VIOLATION" not in _codes(report.errors)


def test_unregistered_compliance_checker_raises_runtime_error():
    spec = build_minimal_agent_spec()
    with pytest.raises(RuntimeError, match="no está registrado"):
        validate_agent_spec(spec, _compliance_profile("nonexistent_check"))


def test_compliance_severity_warning_is_recorded_as_warning_not_error():
    state = build_message_state(say=["Te garantizamos un embarazo exitoso."])
    spec = build_minimal_agent_spec(states=[state])
    report = validate_agent_spec(
        spec, _compliance_profile("no_diagnostic_promises", ComplianceSeverity.WARNING)
    )
    assert "COMPLIANCE_VIOLATION" in _codes(report.warnings)
    assert "COMPLIANCE_VIOLATION" not in _codes(report.errors)


# ---------------------------------------------------------------------------
# Orphan-state report
# ---------------------------------------------------------------------------


def test_orphan_state_report_lists_unreachable_states():
    orphan = build_message_state(state_id="ORPHAN")
    spec = build_minimal_agent_spec(states=[build_message_state(), orphan])
    report_text = build_orphan_state_report(spec)
    assert "ORPHAN" in report_text
    assert "Unreachable states: 1" in report_text


def test_orphan_state_report_treats_start_at_as_a_valid_entry_state():
    spec = build_minimal_agent_spec()
    report_text = build_orphan_state_report(spec)
    assert "Unreachable states: 0" in report_text
