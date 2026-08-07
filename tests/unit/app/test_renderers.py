"""Unit tests for app/renderers.py — System Prompt and Reference Asset rendering."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.classifier import ContentClassifier
from app.renderers import (
    _verbatim_label,
    build_render_context,
    render_all_subflow_documents,
    render_faq,
    render_handler,
    render_prompt,
    render_reference_asset_json,
    render_reference_asset_markdown,
    render_state,
    render_subflow_document,
    render_subflow_index,
    render_system_constants,
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
@pytest.mark.xfail(
    strict=True,
    reason="Known bug (SPEC.md B1): templates/system_prompt.md.j2 renders "
    "[[ input_variables_block ]] twice (once correctly, once dangling at EOF).",
)
def test_real_template_renders_input_variables_section_exactly_once():
    spec = build_minimal_agent_spec()
    classified = ContentClassifier().classify(spec)
    channel_profile = _channel_profile("compliance_and_scope_rules")
    rendered = render_prompt(classified, REAL_TEMPLATE_PATH, channel_profile)
    assert rendered.count("# INPUT VARIABLES") == 1
