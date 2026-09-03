"""Unit tests for app/split_package.py — the deploy-platform re-slice.

The module never re-renders: it slices an already-rendered System Prompt string
into a PERSONALITY/GOAL/INSTRUCTIONS profile document plus a CONVERSATION_FLOW
knowledge base. These tests drive it with a synthetic prompt that mirrors the
real template's H1 structure.
"""

from __future__ import annotations

import pytest

from app.split_package import section_word_counts, split_system_prompt

_PROMPT = """\
# CONVENTIONS
- Convention one.
- Convention two.

# SYSTEM CONSTANTS
| Constant | Description | Value |
| :--- | :--- | :--- |
| <AGENT_NAME> | Name. | Sam |

# INPUT VARIABLES
- `{{current_time}}`: Now.

# AGENT TOOLS
- `end_call`

# IDENTITY
- You are Sam.
- You are warm.

# OBJECTIVES
## PRIMARY_OBJECTIVE
- Book an appointment.

## SECONDARY_OBJECTIVES
- Identify the service.

# GLOBAL OPERATING POLICIES

## STYLE_RULES
- Speak Spanish.

# CONVERSATION_FLOW

## FLOW_DSL_INTERPRETATION
Treat CONVERSATION_FLOW as an executable DSL.

## FLOW_ENTRY
- `START_AT: MSG_START`

## STATES
### STATE MSG_START
- `STATE_ID`: `MSG_START`

# INPUT VARIABLES
- `{{current_time}}`: Now.
"""


def test_split_produces_three_relabelled_fields_in_order():
    doc, _ = split_system_prompt(_PROMPT)
    assert doc.index("# PERSONALITY") < doc.index("# GOAL") < doc.index("# INSTRUCTIONS")
    assert doc.startswith("# PERSONALITY\n")


def test_personality_and_goal_carry_the_identity_and_objectives_bodies():
    doc, _ = split_system_prompt(_PROMPT)
    personality = doc[doc.index("# PERSONALITY") : doc.index("# GOAL")]
    goal = doc[doc.index("# GOAL") : doc.index("# INSTRUCTIONS")]
    assert "You are Sam." in personality
    assert "PRIMARY_OBJECTIVE" in goal and "Book an appointment." in goal
    assert "You are Sam." not in goal


def test_instructions_holds_the_five_head_sections_and_not_the_dsl():
    doc, _ = split_system_prompt(_PROMPT)
    instructions = doc[doc.index("# INSTRUCTIONS") :]
    for header in (
        "# CONVENTIONS",
        "# SYSTEM CONSTANTS",
        "# INPUT VARIABLES",
        "# AGENT TOOLS",
        "# GLOBAL OPERATING POLICIES",
    ):
        assert header in instructions
    assert "FLOW_DSL_INTERPRETATION" not in instructions
    assert "STATE MSG_START" not in instructions


def test_knowledge_base_starts_at_conversation_flow_and_keeps_the_dsl_and_states():
    _, kb = split_system_prompt(_PROMPT)
    assert kb.startswith("# CONVERSATION_FLOW\n")
    assert "FLOW_DSL_INTERPRETATION" in kb
    assert "## STATES" in kb and "STATE MSG_START" in kb


def test_knowledge_base_drops_the_trailing_input_variables_repeat():
    _, kb = split_system_prompt(_PROMPT)
    # The block appears once inside INSTRUCTIONS territory only — the KB's own
    # trailing duplicate (the monolith's recency repeat) is stripped.
    assert "# INPUT VARIABLES" not in kb


def test_no_head_content_is_dropped():
    doc, _ = split_system_prompt(_PROMPT)
    head = _PROMPT.split("\n# CONVERSATION_FLOW")[0]
    for line in head.splitlines():
        stripped = line.strip()
        if not stripped or line.startswith("# "):
            continue
        assert stripped in doc


@pytest.mark.parametrize(
    "missing_header, dropped",
    [
        ("# CONVERSATION_FLOW", "\n# CONVERSATION_FLOW"),
        ("# IDENTITY", "\n# IDENTITY\n- You are Sam.\n- You are warm.\n"),
        ("# OBJECTIVES", "\n# OBJECTIVES\n"),
    ],
)
def test_missing_expected_header_raises_runtimeerror(missing_header, dropped):
    broken = _PROMPT.replace(dropped, "\n", 1)
    with pytest.raises(RuntimeError, match=missing_header.lstrip("# ")):
        split_system_prompt(broken)


def test_section_word_counts_keys_and_values():
    doc, _ = split_system_prompt(_PROMPT)
    counts = section_word_counts(doc)
    assert set(counts) == {"PERSONALITY", "GOAL", "INSTRUCTIONS"}
    assert counts["PERSONALITY"] == len("- You are Sam.\n- You are warm.".split())
    assert counts["INSTRUCTIONS"] > counts["GOAL"] > 0
