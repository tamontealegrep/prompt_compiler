"""Unit tests for app/renderers.py — System Prompt and Reference Asset rendering."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.classifier import ContentClassifier
from app.renderers import (
    _quote,
    _verbatim_label,
    build_render_context,
    build_render_context_mini,
    render_all_subflow_documents,
    render_all_subflow_documents_mini,
    render_faq,
    render_faq_mini,
    render_handler,
    render_handler_mini,
    render_prompt,
    render_prompt_mini,
    render_reference_asset_json,
    render_reference_asset_markdown,
    render_root_states_mini,
    render_state,
    render_state_mini,
    render_states_mini,
    render_subflow_document,
    render_subflow_document_mini,
    render_subflow_index,
    render_system_constants,
    render_terminal_states_mini,
)
from app.schemas import (
    ChannelProfile,
    ChannelType,
    ConstantItem,
    FAQModel,
    HandlerModel,
    PolicySectionDefinition,
    ToolContract,
)
from tests.conftest import build_message_state, build_minimal_agent_spec, build_terminal_state

PROJECT_ROOT = Path(__file__).resolve().parents[3]
REAL_TEMPLATE_PATH = PROJECT_ROOT / "templates" / "system_prompt.md.j2"


def _channel_profile(*section_names: str) -> ChannelProfile:
    return ChannelProfile(
        channel=ChannelType.VOICE,
        display_name="Test Channel",
        policy_sections=[
            PolicySectionDefinition(name=name, label=name.upper(), required=False)
            for name in section_names
        ],
    )


# ---------------------------------------------------------------------------
# Low-level formatting
# ---------------------------------------------------------------------------


def test_verbatim_label_true_renders_verbatim():
    assert _verbatim_label(True) == "[verbatim]"


def test_verbatim_label_false_renders_flexible():
    assert _verbatim_label(False) == "[flexible]"


def test_render_system_constants_includes_name_description_and_value():
    spec = build_minimal_agent_spec(
        constants=[ConstantItem(name="MAX_RETRY", description="Max retries.", value="3")]
    )
    rendered = render_system_constants(spec)
    assert "<MAX_RETRY>" in rendered
    assert "Max retries." in rendered
    assert "| 3 |" in rendered


# ---------------------------------------------------------------------------
# render_handler / render_state — verbatim annotation and section presence
# ---------------------------------------------------------------------------


def test_render_state_marks_verbatim_say_block():
    state = build_message_state(say=["Exact legal text."], say_verbatim=True)
    rendered = render_state(state)
    assert "`SAY` [verbatim]" in rendered
    assert '"Exact legal text."' in rendered


def test_render_state_marks_flexible_say_block_by_default():
    state = build_message_state(say=["Paraphrasable text."])
    rendered = render_state(state)
    assert "`SAY` [flexible]" in rendered


def test_render_state_omits_capture_block_when_empty():
    state = build_message_state()
    rendered = render_state(state)
    assert "`CAPTURE`" not in rendered


def test_render_state_includes_capture_block_when_present():
    state = build_message_state(
        type="question", wait="yes", say=["Q?"], capture=[{"slot": "caller_age", "type_expr": "int"}]
    )
    rendered = render_state(state)
    assert "`CAPTURE`" in rendered
    assert "[caller_age]" in rendered


def test_render_state_includes_execute_block_for_action_states():
    state = build_message_state(
        state_id="BOOK", type="action", say=[], execute="book_appointment", route=["GO_TO: CLOSE"]
    )
    rendered = render_state(state)
    assert "EXECUTE" in rendered
    assert "CALL_TOOL:book_appointment" in rendered


def test_render_handler_includes_trigger_block():
    handler = HandlerModel.model_validate(
        {
            "handler_id": "H_HUMAN",
            "type": "message",
            "say": ["Transferring you now."],
            "wait": "no",
            "trigger": ["user asks for a human agent"],
            "route": ["GO_TO: HANDOFF"],
        }
    )
    rendered = render_handler(handler)
    assert "TRIGGER" in rendered
    assert "user asks for a human agent" in rendered


def test_render_faq_includes_resume_to_when_present():
    faq = FAQModel.model_validate(
        {
            "faq_id": "FAQ_PRICE",
            "type": "message",
            "match": ["how much does it cost"],
            "say": ["Pricing varies by service."],
            "resume_to": "[current_state]",
        }
    )
    rendered = render_faq(faq)
    assert "RESUME_TO" in rendered
    assert "[current_state]" in rendered


# ---------------------------------------------------------------------------
# Subflow-aware rendering
# ---------------------------------------------------------------------------


def _spec_with_subflow_states():
    entry = build_message_state("CALLBACK__ASK_TIME", type="start", say=[], route=["GO_TO: CALLBACK__DONE"])
    exit_state = build_terminal_state("CALLBACK__DONE")
    root = build_message_state("GREETING", route=["GO_TO: CALLBACK__ASK_TIME"])
    return build_minimal_agent_spec(states=[root, entry], terminal_states=[exit_state])


def test_render_subflow_index_lists_detected_namespaces():
    # Namespaces are derived from the state_id prefix (state ids are
    # UPPER_SNAKE_CASE per the DSL), so the namespace itself surfaces
    # uppercase here — unlike the lowercase `namespace:` field an author
    # writes in the manifest, which only controls slot-name casing.
    spec = _spec_with_subflow_states()
    rendered = render_subflow_index(spec)
    assert "`CALLBACK`" in rendered
    assert "CALLBACK__ASK_TIME" in rendered


def test_render_subflow_index_empty_when_no_namespaces():
    spec = build_minimal_agent_spec()
    assert render_subflow_index(spec) == ""


def test_render_all_subflow_documents_returns_one_per_namespace():
    spec = _spec_with_subflow_states()
    documents = render_all_subflow_documents(spec)
    assert set(documents.keys()) == {"CALLBACK"}
    assert "CALLBACK__ASK_TIME" in documents["CALLBACK"]


def test_render_subflow_document_shows_dynamic_exit_target_as_dash_known_gap():
    """SPEC.md B5: the subflow-document exit table uses a narrower local regex
    than app.utils.extract_goto_targets and cannot see dynamic ``[slot]``
    exits, so it currently renders '-' instead of the real dynamic target.
    This test locks in the CURRENT (buggy) behavior as a regression signal —
    see the strict-xfail companion test below for the intended behavior.
    """
    exit_state = build_message_state(
        "CALLBACK__EXIT", type="subflow_change", say=[], route=["GO_TO: [resume_state]"]
    )
    spec = build_minimal_agent_spec(states=[build_message_state(), exit_state])
    document = render_subflow_document(spec, "CALLBACK")
    assert "CALLBACK__EXIT` → —" in document


@pytest.mark.xfail(
    strict=True,
    reason="Known gap (SPEC.md B5): render_subflow_document's local _extract_goto_targets "
    "regex doesn't support dynamic [slot] targets, unlike app.utils.extract_goto_targets.",
)
def test_render_subflow_document_shows_dynamic_exit_target_intended_behavior():
    exit_state = build_message_state(
        "CALLBACK__EXIT", type="subflow_change", say=[], route=["GO_TO: [resume_state]"]
    )
    spec = build_minimal_agent_spec(states=[build_message_state(), exit_state])
    document = render_subflow_document(spec, "CALLBACK")
    exits_line = next(line for line in document.splitlines() if line.startswith("- `CALLBACK__EXIT`"))
    assert "[resume_state]" in exits_line


# ---------------------------------------------------------------------------
# Reference Asset rendering
# ---------------------------------------------------------------------------


def test_render_reference_asset_markdown_includes_faqs_and_contracts():
    faq = FAQModel.model_validate(
        {"faq_id": "FAQ_A", "type": "message", "match": ["hi"], "say": ["Hello."]}
    )
    contract = ToolContract.model_validate(
        {"name": "book_appointment", "description": "Books it.", "inputs": [], "outputs": []}
    )
    spec = build_minimal_agent_spec(
        faqs=[faq], tools=["book_appointment"], tool_contracts=[contract]
    )
    classified = ContentClassifier().classify(spec)
    markdown = render_reference_asset_markdown(classified)
    assert "FAQ_A" in markdown
    assert "book_appointment" in markdown


def test_render_reference_asset_json_has_stable_top_level_keys():
    spec = build_minimal_agent_spec()
    classified = ContentClassifier().classify(spec)
    payload = render_reference_asset_json(classified)
    assert set(payload.keys()) == {"agent_id", "context", "tool_contracts", "faqs"}


def test_render_reference_asset_json_faq_includes_say_verbatim_flag():
    faq = FAQModel.model_validate(
        {
            "faq_id": "FAQ_A",
            "type": "message",
            "match": ["hi"],
            "say": ["Hello."],
            "say_verbatim": True,
        }
    )
    spec = build_minimal_agent_spec(faqs=[faq])
    classified = ContentClassifier().classify(spec)
    payload = render_reference_asset_json(classified)
    assert payload["faqs"][0]["say_verbatim"] is True


# ---------------------------------------------------------------------------
# build_render_context / render_prompt
# ---------------------------------------------------------------------------


def test_build_render_context_embed_subflows_true_includes_all_states():
    spec = _spec_with_subflow_states()
    classified = ContentClassifier().classify(spec)
    context = build_render_context(classified, _channel_profile(), embed_subflows=True)
    assert "CALLBACK__ASK_TIME" in context["states_block"]
    assert context["subflow_index_block"] == ""


def test_build_render_context_embed_subflows_false_excludes_subflow_states():
    spec = _spec_with_subflow_states()
    classified = ContentClassifier().classify(spec)
    context = build_render_context(classified, _channel_profile(), embed_subflows=False)
    # The root state's own ROUTE line legitimately mentions the subflow state
    # id as a GO_TO target; what must be absent is the subflow state's own
    # rendered block (its "### STATE ..." header).
    assert "### STATE CALLBACK__ASK_TIME" not in context["states_block"]
    assert "### STATE GREETING" in context["states_block"]
    assert context["subflow_index_block"] != ""


def test_render_prompt_uses_custom_delimiters_and_fills_named_blocks(tmp_path):
    template_path = tmp_path / "tiny_template.md.j2"
    template_path.write_text(
        "IDENTITY:\n[[ identity_block ]]\n\nSTATES:\n[[ states_block ]]\n", encoding="utf-8"
    )
    spec = build_minimal_agent_spec()
    classified = ContentClassifier().classify(spec)
    rendered = render_prompt(classified, template_path, _channel_profile())
    assert "# IDENTITY" in rendered
    assert "GREETING" in rendered


def test_render_prompt_raises_file_not_found_for_missing_template(tmp_path):
    spec = build_minimal_agent_spec()
    classified = ContentClassifier().classify(spec)
    with pytest.raises(FileNotFoundError):
        render_prompt(classified, tmp_path / "does_not_exist.j2", _channel_profile())


def test_render_prompt_does_not_leak_double_curly_jinja_syntax(tmp_path):
    """The custom [[ ]] delimiters must not collide with the DSL's own {{var}} syntax."""
    template_path = tmp_path / "tiny_template.md.j2"
    template_path.write_text("[[ input_variables_block ]]", encoding="utf-8")
    spec = build_minimal_agent_spec()
    classified = ContentClassifier().classify(spec)
    rendered = render_prompt(classified, template_path, _channel_profile())
    assert "{{caller_name}}" not in rendered  # no declared input var named that in the fixture
    assert "# INPUT VARIABLES" in rendered


# ---------------------------------------------------------------------------
# The real project template (regression coverage for SPEC.md B1)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not REAL_TEMPLATE_PATH.exists(), reason="real template not found")
def test_real_template_renders_all_named_blocks_without_raising():
    spec = build_minimal_agent_spec()
    classified = ContentClassifier().classify(spec)
    channel_profile = _channel_profile("compliance_and_scope_rules")
    rendered = render_prompt(classified, REAL_TEMPLATE_PATH, channel_profile)
    assert "# CONVERSATION_FLOW" in rendered
    assert "GREETING" in rendered


@pytest.mark.skipif(not REAL_TEMPLATE_PATH.exists(), reason="real template not found")
def test_real_template_renders_input_variables_section_twice_by_design():
    """SPEC.md B1 (reclassified 2026-08-06): confirmed intentional, not a bug.

    templates/system_prompt.md.j2 renders [[ input_variables_block ]] twice
    on purpose. This locks in the current, confirmed-correct behavior — do
    not "fix" this to assert count == 1.
    """
    spec = build_minimal_agent_spec()
    classified = ContentClassifier().classify(spec)
    channel_profile = _channel_profile("compliance_and_scope_rules")
    rendered = render_prompt(classified, REAL_TEMPLATE_PATH, channel_profile)
    assert rendered.count("# INPUT VARIABLES") == 2


# ---------------------------------------------------------------------------
# Compact ("mini") renderers
# ---------------------------------------------------------------------------


def _state(**overrides):
    from app.schemas import StateModel

    data = {
        "state_id": "S_TEST",
        "type": "message",
        "say": ["Hello."],
        "wait": "no",
        "route": ["GO_TO: NEXT"],
    }
    data.update(overrides)
    return StateModel.model_validate(data)


def test_render_state_mini_message_inlines_single_route_and_drops_wait_and_type_word():
    state = _state(state_id="GREETING", say=["Hello, this is a test greeting."], route=["GO_TO: CLOSE"])
    rendered = render_state_mini(state)
    expected = "MSG GREETING  GO_TO: CLOSE\n  SAY [flex]: " + _quote("Hello, this is a test greeting.")
    assert rendered == expected


def test_render_state_mini_never_shows_wait_or_final_labels():
    state = _state()
    rendered = render_state_mini(state)
    assert "WAIT" not in rendered
    assert "FINAL" not in rendered


def test_render_state_mini_question_shows_capture_inline_and_suppresses_default_store():
    state = _state(
        state_id="ASK_NAME",
        type="question",
        say=["What is your name?"],
        wait="yes",
        capture=[{"slot": "name", "type_expr": "free_text"}],
        store=["[name] = [name]"],
        route=["GO_TO: DECIDE_NAME"],
    )
    rendered = render_state_mini(state)
    expected = "Q ASK_NAME  CAPTURE: name:free_text  GO_TO: DECIDE_NAME\n  SAY [flex]: " + _quote(
        "What is your name?"
    )
    assert rendered == expected
    assert "STORE" not in rendered


def test_render_state_mini_question_shows_store_when_it_deviates_from_the_echo_default():
    state = _state(
        state_id="ASK_NAME",
        type="question",
        say=["What is your name?"],
        wait="yes",
        capture=[{"slot": "raw_name", "type_expr": "free_text"}],
        store=["[name] = normalize(raw_name)"],
        route=["GO_TO: DECIDE_NAME"],
    )
    rendered = render_state_mini(state)
    assert "STORE: [name] = normalize(raw_name)" in rendered


def test_render_state_mini_decision_never_shows_say_and_keeps_multi_branch_route_as_a_block():
    state = _state(
        state_id="DECIDE_NAME",
        type="decision",
        say=[],
        wait="no",
        do=["[name_try] = [name_try] + 1"],
        route=[
            "IF [name_try] >= <MAX_RETRY_ATTEMPTS> -> GO_TO: FLOW_EXIT",
            "IF [name] IS NULL -> GO_TO: ASK_NAME",
            "IF [name] == 'yes' -> GO_TO: NEXT",
            "GO_TO: ASK_NAME",
        ],
    )
    rendered = render_state_mini(state)
    assert rendered == (
        "DEC DECIDE_NAME\n"
        "  DO: [name_try] = [name_try] + 1\n"
        "  ROUTE:\n"
        "    IF [name_try] >= <MAX_RETRY_ATTEMPTS> -> GO_TO: FLOW_EXIT\n"
        "    IF [name] IS NULL -> GO_TO: ASK_NAME\n"
        "    IF [name] == 'yes' -> GO_TO: NEXT\n"
        "    GO_TO: ASK_NAME"
    )
    assert "SAY" not in rendered


def test_render_state_mini_action_shows_execute_inline_with_no_boilerplate():
    state = _state(
        state_id="SC_AVAIL",
        type="action",
        say=[],
        wait="no",
        do=["TOOL CALL ONLY: call get_available_slots now."],
        capture=[{"slot": "available_slots", "type_expr": "list"}],
        execute="get_available_slots",
        route=[
            "IF get_available_slots.has_availability == true "
            "AND [available_slots] IS NOT NULL -> GO_TO: SC_DAYS"
        ],
        fallback=["GO_TO: SC_NO_AVAIL"],
    )
    rendered = render_state_mini(state)
    assert "EXECUTE: get_available_slots" in rendered
    assert "CAPTURE: available_slots:list" in rendered
    assert "NEXT_ASSISTANT_ACTION" not in rendered
    assert "SPEECH_BEFORE_TOOL" not in rendered
    assert "FALLBACK:" in rendered
    assert "GO_TO: SC_NO_AVAIL" in rendered


def test_render_state_mini_terminal_shows_execute_and_say_with_no_final_label():
    state = _state(
        state_id="OP_END_NO",
        type="terminal",
        say=["Goodbye."],
        wait="no",
        execute="end_call",
        route=[],
        final="yes",
    )
    rendered = render_state_mini(state)
    expected = "END OP_END_NO  EXECUTE: end_call\n  SAY [flex]: " + _quote("Goodbye.")
    assert rendered == expected


def test_render_state_mini_start_is_a_single_line_when_it_has_no_extra_fields():
    state = _state(state_id="FLOW_START", type="start", say=[], route=["GO_TO: FLOW_INIT"])
    rendered = render_state_mini(state)
    assert rendered == "START FLOW_START  GO_TO: FLOW_INIT"


def test_render_state_mini_subflow_change_is_a_single_line_with_do_note():
    state = _state(
        state_id="OP_TO_Q",
        type="subflow_change",
        say=[],
        do=["Load the reference document for the Q subflow before continuing."],
        route=["GO_TO: Q__SQ_START"],
    )
    rendered = render_state_mini(state)
    assert rendered == (
        "CHANGE OP_TO_Q  GO_TO: Q__SQ_START\n"
        "  DO: Load the reference document for the Q subflow before continuing."
    )


def test_render_state_mini_multiple_captures_render_as_a_tuple():
    state = _state(
        state_id="ASK_BOTH",
        type="question",
        say=["Q?"],
        wait="yes",
        capture=[
            {"slot": "a", "type_expr": "str"},
            {"slot": "b", "type_expr": "int"},
        ],
        route=["GO_TO: NEXT"],
    )
    rendered = render_state_mini(state)
    assert "CAPTURE: (a:str, b:int)" in rendered


def test_render_state_mini_multi_line_goal_renders_as_a_block():
    state = _state(goal=["First reason.", "Second reason."])
    rendered = render_state_mini(state)
    assert "GOAL:\n    First reason.\n    Second reason." in rendered


def test_render_state_mini_verbatim_say_uses_short_tag():
    state = _state(say=["Exact legal text."], say_verbatim=True)
    rendered = render_state_mini(state)
    assert "SAY [verb]: " + _quote("Exact legal text.") in rendered


def test_render_state_mini_route_with_fallback_stays_a_block_even_if_route_is_a_single_line():
    state = _state(
        state_id="FLOW_HAS_NAME",
        type="decision",
        say=[],
        do=["Evaluate whether {{contact_name}} is available and not NULL."],
        route=["IF {{contact_name}} IS NOT NULL -> GO_TO: FLOW_NEXT"],
        fallback=["GO_TO: FLOW_ASK_NAME"],
    )
    rendered = render_state_mini(state)
    assert "ROUTE:\n    IF {{contact_name}} IS NOT NULL -> GO_TO: FLOW_NEXT" in rendered
    assert "FALLBACK:\n    GO_TO: FLOW_ASK_NAME" in rendered


def test_render_handler_mini_includes_trigger_and_multi_branch_route():
    handler = HandlerModel.model_validate(
        {
            "handler_id": "HNDLR_REPEAT",
            "type": "message",
            "trigger": ["can you repeat that", "I did not hear you"],
            "say": ["Sure, let me repeat that."],
            "do": ["[repeat_count] = [repeat_count] + 1"],
            "wait": "no",
            "route": [
                "IF [repeat_count] < <MAX_REPEAT_ATTEMPTS> -> GO_TO: [current_state]",
                "IF [repeat_count] >= <MAX_REPEAT_ATTEMPTS> -> GO_TO: FLOW_NO_INPUT_EXIT",
            ],
        }
    )
    rendered = render_handler_mini(handler)
    assert rendered.startswith("MSG HNDLR_REPEAT\n")
    assert "TRIGGER: " + _quote("can you repeat that") + " | " + _quote("I did not hear you") in rendered
    assert "DO: [repeat_count] = [repeat_count] + 1" in rendered
    assert "SAY [flex]: " + _quote("Sure, let me repeat that.") in rendered
    assert "ROUTE:" in rendered


def test_render_faq_mini_has_no_type_tag_and_joins_match_phrases():
    faq = FAQModel.model_validate(
        {
            "faq_id": "FAQ_PRICE",
            "type": "message",
            "match": ["how much does it cost", "what is the price"],
            "say": ["Pricing varies by service."],
            "resume_to": "[current_state]",
        }
    )
    rendered = render_faq_mini(faq)
    expected = (
        "FAQ_PRICE  RESUME_TO: [current_state]\n"
        "  MATCH: " + _quote("how much does it cost") + " | " + _quote("what is the price") + "\n"
        "  SAY [flex]: " + _quote("Pricing varies by service.")
    )
    assert rendered == expected
    assert not rendered.startswith(("MSG", "Q ", "DEC", "ACT", "REG", "END", "START", "CHANGE"))


def test_render_faq_mini_omits_resume_to_line_when_absent():
    faq = FAQModel.model_validate(
        {"faq_id": "FAQ_A", "type": "message", "match": ["hi"], "say": ["Hello."]}
    )
    rendered = render_faq_mini(faq)
    assert "RESUME_TO" not in rendered


def test_render_state_mini_is_meaningfully_smaller_than_the_full_renderer():
    state = _state(
        state_id="ASK_NAME",
        type="question",
        say=["What is your name?"],
        wait="yes",
        capture=[{"slot": "name", "type_expr": "free_text"}],
        store=["[name] = [name]"],
        route=["GO_TO: DECIDE_NAME"],
    )
    assert len(render_state_mini(state)) < len(render_state(state))


# ---------------------------------------------------------------------------
# Section-level mini renderers and build_render_context_mini
# ---------------------------------------------------------------------------


def test_render_states_mini_joins_with_blank_line():
    spec = build_minimal_agent_spec(
        states=[build_message_state("A", route=["GO_TO: B"]), build_message_state("B", route=["GO_TO: A"])]
    )
    rendered = render_states_mini(spec)
    assert "\n\n" in rendered
    first, second = rendered.split("\n\n")
    assert first.startswith("MSG A  GO_TO: B")
    assert second.startswith("MSG B  GO_TO: A")


def test_render_terminal_states_mini_uses_end_tag():
    spec = build_minimal_agent_spec()
    rendered = render_terminal_states_mini(spec)
    assert rendered.startswith("END CLOSE")


def _spec_with_subflow_states_mini():
    entry = build_message_state("CALLBACK__ASK_TIME", type="start", say=[], route=["GO_TO: CALLBACK__DONE"])
    exit_state = build_terminal_state("CALLBACK__DONE")
    root = build_message_state("GREETING", route=["GO_TO: CALLBACK__ASK_TIME"])
    return build_minimal_agent_spec(states=[root, entry], terminal_states=[exit_state])


def test_render_root_states_mini_excludes_subflow_states():
    spec = _spec_with_subflow_states_mini()
    rendered = render_root_states_mini(spec)
    # The root state's own GO_TO line legitimately mentions the subflow
    # state id as a target; what must be absent is that state's own header.
    assert "MSG GREETING" in rendered
    assert "START CALLBACK__ASK_TIME" not in rendered


def test_render_all_subflow_documents_mini_returns_one_per_namespace():
    spec = _spec_with_subflow_states_mini()
    documents = render_all_subflow_documents_mini(spec)
    assert set(documents.keys()) == {"CALLBACK"}
    assert "START CALLBACK__ASK_TIME" in documents["CALLBACK"]


def test_render_subflow_document_mini_lists_entry_state():
    spec = _spec_with_subflow_states_mini()
    document = render_subflow_document_mini(spec, "CALLBACK")
    assert "Entry: CALLBACK__ASK_TIME" in document


def test_build_render_context_mini_embed_subflows_true_includes_all_states():
    spec = _spec_with_subflow_states_mini()
    classified = ContentClassifier().classify(spec)
    context = build_render_context_mini(classified, _channel_profile(), embed_subflows=True)
    assert "CALLBACK__ASK_TIME" in context["states_block"]
    assert context["subflow_index_block"] == ""


def test_build_render_context_mini_embed_subflows_false_excludes_subflow_states():
    spec = _spec_with_subflow_states_mini()
    classified = ContentClassifier().classify(spec)
    context = build_render_context_mini(classified, _channel_profile(), embed_subflows=False)
    assert "START CALLBACK__ASK_TIME" not in context["states_block"]
    assert "MSG GREETING" in context["states_block"]


def test_build_render_context_mini_reuses_non_per_node_blocks_unchanged():
    spec = build_minimal_agent_spec()
    classified = ContentClassifier().classify(spec)
    full_context = build_render_context(classified, _channel_profile())
    mini_context = build_render_context_mini(classified, _channel_profile())
    for key in ("system_constants_block", "input_variables_block", "identity_block", "objectives_block"):
        assert full_context[key] == mini_context[key]


def test_render_prompt_mini_uses_compact_blocks(tmp_path):
    template_path = tmp_path / "tiny_mini_template.md.j2"
    template_path.write_text("STATES:\n[[ states_block ]]\n", encoding="utf-8")
    spec = build_minimal_agent_spec()
    classified = ContentClassifier().classify(spec)
    rendered = render_prompt_mini(classified, template_path, _channel_profile())
    assert "MSG GREETING" in rendered
    assert "### STATE" not in rendered


def test_render_prompt_mini_raises_file_not_found_for_missing_template(tmp_path):
    spec = build_minimal_agent_spec()
    classified = ContentClassifier().classify(spec)
    with pytest.raises(FileNotFoundError):
        render_prompt_mini(classified, tmp_path / "does_not_exist.j2", _channel_profile())
