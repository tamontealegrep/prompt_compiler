"""Unit tests for app/schemas.py — DSL grammar invariants (FlowObjectBase, SubflowTemplate)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import (
    CaptureField,
    HandlerModel,
    StateModel,
    SubflowExports,
    SubflowTemplate,
    TemplateParamDefinition,
)


def _base_state(**overrides) -> dict:
    data = {
        "state_id": "S_TEST",
        "type": "message",
        "say": ["Hello."],
        "wait": "no",
        "route": ["GO_TO: NEXT"],
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# type=message
# ---------------------------------------------------------------------------


def test_message_state_valid_construction_succeeds():
    state = StateModel.model_validate(_base_state())
    assert state.type == "message"


def test_message_state_requires_wait_no():
    with pytest.raises(ValidationError, match="wait='no'"):
        StateModel.model_validate(_base_state(wait="yes"))


def test_message_state_requires_say():
    with pytest.raises(ValidationError, match="SAY"):
        StateModel.model_validate(_base_state(say=[]))


def test_message_state_forbids_execute():
    with pytest.raises(ValidationError, match="EXECUTE"):
        StateModel.model_validate(_base_state(execute="some_tool"))


# ---------------------------------------------------------------------------
# type=question
# ---------------------------------------------------------------------------


def test_question_state_requires_wait_yes():
    with pytest.raises(ValidationError, match="wait='yes'"):
        StateModel.model_validate(
            _base_state(type="question", wait="no", say=["What is your name?"])
        )


def test_question_state_requires_say():
    with pytest.raises(ValidationError, match="SAY"):
        StateModel.model_validate(_base_state(type="question", wait="yes", say=[]))


def test_question_state_valid_construction_succeeds():
    state = StateModel.model_validate(
        _base_state(type="question", wait="yes", say=["What is your name?"])
    )
    assert state.type == "question"


# ---------------------------------------------------------------------------
# type=decision
# ---------------------------------------------------------------------------


def test_decision_state_forbids_say():
    with pytest.raises(ValidationError, match="SAY"):
        StateModel.model_validate(
            _base_state(type="decision", wait="no", say=["Should not be here."])
        )


def test_decision_state_forbids_wait_yes():
    with pytest.raises(ValidationError, match="wait='yes'"):
        StateModel.model_validate(_base_state(type="decision", wait="yes", say=[]))


def test_decision_state_valid_construction_with_explicit_wait_no():
    state = StateModel.model_validate(_base_state(type="decision", wait="no", say=[]))
    assert state.type == "decision"


def test_decision_state_valid_construction_with_wait_omitted():
    data = _base_state(type="decision", say=[])
    del data["wait"]
    state = StateModel.model_validate(data)
    assert state.wait is None


# ---------------------------------------------------------------------------
# type=action
# ---------------------------------------------------------------------------


def test_action_state_requires_execute():
    with pytest.raises(ValidationError, match="EXECUTE"):
        StateModel.model_validate(_base_state(type="action", wait="no", say=[]))


def test_action_state_valid_construction_succeeds():
    state = StateModel.model_validate(
        _base_state(type="action", wait="no", say=[], execute="book_appointment")
    )
    assert state.execute == "book_appointment"


# ---------------------------------------------------------------------------
# type=registration
# ---------------------------------------------------------------------------


def test_registration_state_forbids_execute():
    with pytest.raises(ValidationError, match="EXECUTE"):
        StateModel.model_validate(
            _base_state(type="registration", wait="no", say=[], execute="some_tool")
        )


def test_registration_state_valid_construction_succeeds():
    state = StateModel.model_validate(_base_state(type="registration", wait="no", say=[]))
    assert state.type == "registration"


# ---------------------------------------------------------------------------
# type=terminal
# ---------------------------------------------------------------------------


def test_terminal_state_requires_final_yes():
    with pytest.raises(ValidationError, match="final='yes'"):
        StateModel.model_validate(
            {
                "state_id": "S_END",
                "type": "terminal",
                "say": ["Goodbye."],
                "wait": "no",
            }
        )


def test_terminal_state_does_not_require_route_or_fallback():
    state = StateModel.model_validate(
        {
            "state_id": "S_END",
            "type": "terminal",
            "say": ["Goodbye."],
            "wait": "no",
            "final": "yes",
        }
    )
    assert state.route == []
    assert state.fallback == []


# ---------------------------------------------------------------------------
# type=start / subflow_change
# ---------------------------------------------------------------------------


def test_start_state_forbids_final_yes():
    with pytest.raises(ValidationError, match="final='yes'"):
        StateModel.model_validate(
            _base_state(type="start", wait="no", say=[], final="yes")
        )


def test_subflow_change_state_forbids_final_yes():
    with pytest.raises(ValidationError, match="final='yes'"):
        StateModel.model_validate(
            _base_state(type="subflow_change", wait="no", say=[], final="yes")
        )


# ---------------------------------------------------------------------------
# Non-terminal objects require ROUTE or FALLBACK
# ---------------------------------------------------------------------------


def test_non_terminal_state_without_route_or_fallback_is_rejected():
    with pytest.raises(ValidationError, match=r"ROUTE.*FALLBACK|FALLBACK.*ROUTE"):
        StateModel.model_validate(_base_state(route=[], fallback=[]))


def test_non_terminal_state_with_only_fallback_is_valid():
    state = StateModel.model_validate(_base_state(route=[], fallback=["GO_TO: SAFE_EXIT"]))
    assert state.fallback == ["GO_TO: SAFE_EXIT"]


# ---------------------------------------------------------------------------
# Known DSL grammar gaps (SPEC.md B2) — documented via strict xfail so a
# future fix (SPEC.md roadmap phase 20) flips these to passing and the
# marker must be removed.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="Known gap (SPEC.md B2): only message/registration forbid EXECUTE and "
    "action requires it — question/decision/terminal/start/subflow_change have no "
    "constraint, so a question node can carry an EXECUTE block.",
)
def test_question_state_forbids_execute():
    with pytest.raises(ValidationError):
        StateModel.model_validate(
            _base_state(
                type="question", wait="yes", say=["Q?"], execute="some_tool"
            )
        )


@pytest.mark.xfail(
    strict=True,
    reason="Known gap (SPEC.md B2): final='yes' is only constrained for "
    "terminal/start/subflow_change — a message node can set final='yes' without "
    "type=terminal, and app.utils.terminal_state_ids() will treat it as terminal.",
)
def test_message_state_forbids_final_yes():
    with pytest.raises(ValidationError):
        StateModel.model_validate(_base_state(type="message", final="yes"))


# ---------------------------------------------------------------------------
# HandlerModel-specific fields
# ---------------------------------------------------------------------------


def test_handler_requires_non_empty_trigger():
    with pytest.raises(ValidationError, match="trigger"):
        HandlerModel.model_validate(
            {
                "handler_id": "H_TEST",
                "type": "message",
                "say": ["Handling."],
                "wait": "no",
                "trigger": [],
                "route": ["GO_TO: SOME_STATE"],
            }
        )


def test_handler_valid_construction_succeeds():
    handler = HandlerModel.model_validate(
        {
            "handler_id": "H_TEST",
            "type": "message",
            "say": ["Handling."],
            "wait": "no",
            "trigger": ["user asks to speak to a human"],
            "route": ["GO_TO: HUMAN_HANDOFF"],
        }
    )
    assert handler.handler_id == "H_TEST"


# ---------------------------------------------------------------------------
# Regex-constrained identifiers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_id", ["lower_case_id", "1STARTS_WITH_DIGIT", "HAS SPACE", ""])
def test_state_id_rejects_invalid_upper_snake_case(bad_id):
    with pytest.raises(ValidationError):
        StateModel.model_validate(_base_state(state_id=bad_id))


def test_capture_field_requires_lower_snake_case_slot():
    with pytest.raises(ValidationError):
        CaptureField.model_validate({"slot": "NotSnakeCase", "type_expr": "str"})


def test_capture_field_valid_construction_succeeds():
    field = CaptureField.model_validate({"slot": "caller_age", "type_expr": "int"})
    assert field.slot == "caller_age"


# ---------------------------------------------------------------------------
# SubflowTemplate
# ---------------------------------------------------------------------------


def _minimal_template(**overrides) -> dict:
    data = {
        "template_id": "callback",
        "description": "A callback scheduling subflow.",
        "states": [_base_state(state_id="ASK_TIME", route=["GO_TO: DONE"])],
        "terminal_states": [
            {
                "state_id": "DONE",
                "type": "terminal",
                "say": ["Scheduled."],
                "wait": "no",
                "final": "yes",
            }
        ],
    }
    data.update(overrides)
    return data


def test_subflow_template_valid_construction_succeeds():
    template = SubflowTemplate.model_validate(_minimal_template())
    assert template.template_id == "callback"


def test_subflow_template_requires_some_content():
    with pytest.raises(ValidationError, match="no aporta ningún contenido"):
        SubflowTemplate.model_validate(
            {"template_id": "empty_template", "description": "Nothing here."}
        )


def test_subflow_template_rejects_duplicate_param_names():
    with pytest.raises(ValidationError, match="Parámetro duplicado"):
        SubflowTemplate.model_validate(
            _minimal_template(
                params=[
                    TemplateParamDefinition(name="agent_name").model_dump(),
                    TemplateParamDefinition(name="agent_name").model_dump(),
                ]
            )
        )


def test_subflow_template_export_must_reference_existing_state():
    with pytest.raises(ValidationError, match="no existe dentro del template"):
        SubflowTemplate.model_validate(
            _minimal_template(exports={"states": {"entry": "STATE_DOES_NOT_EXIST"}})
        )


def test_subflow_template_export_referencing_real_state_is_valid():
    template = SubflowTemplate.model_validate(
        _minimal_template(exports={"states": {"entry": "ASK_TIME"}})
    )
    assert template.exports.states == {"entry": "ASK_TIME"}


def test_subflow_template_terminal_states_must_be_type_terminal():
    with pytest.raises(ValidationError):
        SubflowTemplate.model_validate(
            _minimal_template(
                terminal_states=[_base_state(state_id="NOT_ACTUALLY_TERMINAL")]
            )
        )


def test_subflow_exports_validates_state_export_value_is_upper_id():
    with pytest.raises(ValidationError):
        SubflowExports.model_validate({"states": {"entry": "not_upper_case"}})
