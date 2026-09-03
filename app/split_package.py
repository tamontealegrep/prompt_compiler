"""Re-slice a rendered System Prompt into a deploy-platform "split" package.

Some hosting platforms cap the agent profile field that holds identity + goal +
behavioral instructions (well under the size of a full flow) and expect the
conversational flow to live in a separately-indexed knowledge base. This module
splits the single rendered System Prompt into those two documents:

- **split system prompt** — ``# PERSONALITY`` (identity), ``# GOAL`` (objectives)
  and ``# INSTRUCTIONS`` (conventions, system constants, input variables, agent
  tools and global operating policies).
- **knowledge base** — everything under ``# CONVERSATION_FLOW`` (the DSL
  interpretation rules, handlers, FAQs, flow policy and every state).

It does not re-render anything: every byte it emits came from
:func:`app.renderers.render_prompt` / ``render_prompt_mini``, so the platform
sees exactly the text the monolithic prompt would have carried.
"""

from __future__ import annotations

_CONVERSATION_FLOW_HEADER = "\n# CONVERSATION_FLOW"
_INPUT_VARIABLES_HEADER = "\n# INPUT VARIABLES"

# Head-of-prompt H1 sections that make up the INSTRUCTIONS profile field, in the
# order the templates render them. IDENTITY and OBJECTIVES are pulled out
# separately (they become PERSONALITY and GOAL); everything under
# CONVERSATION_FLOW goes to the knowledge base.
_INSTRUCTIONS_SECTIONS = (
    "CONVENTIONS",
    "SYSTEM CONSTANTS",
    "INPUT VARIABLES",
    "AGENT TOOLS",
    "GLOBAL OPERATING POLICIES",
)

SPLIT_SYSTEM_PROMPT_WORD_LIMIT = 2000
"""Soft ceiling for each PERSONALITY / GOAL / INSTRUCTIONS field. The CLI flags
any section above this; it never fails the build (platforms differ)."""


def _split_h1(text: str) -> dict[str, str]:
    """Map each ``# NAME`` H1 section title to its body (header line excluded)."""
    sections: dict[str, str] = {}
    current: str | None = None
    body: list[str] = []
    for line in text.split("\n"):
        if line.startswith("# "):
            if current is not None:
                sections[current] = "\n".join(body).strip("\n")
            current = line[2:].strip()
            body = []
        elif current is not None:
            body.append(line)
    if current is not None:
        sections[current] = "\n".join(body).strip("\n")
    return sections


def _require(sections: dict[str, str], name: str) -> str:
    if name not in sections:
        raise RuntimeError(
            f"Cannot build the split package: the rendered System Prompt has no "
            f"'# {name}' section. Expected sections: "
            f"{', '.join(('IDENTITY', 'OBJECTIVES', *_INSTRUCTIONS_SECTIONS))}."
        )
    return sections[name]


def split_system_prompt(system_prompt: str) -> tuple[str, str]:
    """Split a rendered System Prompt into ``(split_system_prompt, knowledge_base)``.

    Parameters:
        system_prompt (str): A full rendered System Prompt (standard or mini).

    Returns:
        tuple[str, str]: The 3-section profile document (PERSONALITY / GOAL /
        INSTRUCTIONS) and the CONVERSATION_FLOW knowledge base.

    Raises:
        RuntimeError: If the ``# CONVERSATION_FLOW`` boundary or any expected
        head section (IDENTITY, OBJECTIVES, or an INSTRUCTIONS section) is absent.
    """
    head, sep, flow = system_prompt.partition(_CONVERSATION_FLOW_HEADER)
    if not sep:
        raise RuntimeError(
            "Cannot build the split package: the rendered System Prompt has no "
            "'# CONVERSATION_FLOW' section."
        )

    sections = _split_h1(head)
    identity = _require(sections, "IDENTITY")
    objectives = _require(sections, "OBJECTIVES")
    instructions_body = "\n\n".join(
        f"# {name}\n{_require(sections, name)}" for name in _INSTRUCTIONS_SECTIONS
    )

    split_doc = (
        f"# PERSONALITY\n\n{identity}\n\n"
        f"# GOAL\n\n{objectives}\n\n"
        f"# INSTRUCTIONS\n\n{instructions_body}\n"
    )

    knowledge_base = "# CONVERSATION_FLOW" + flow
    # Drop the monolith's trailing INPUT VARIABLES recency-repeat — in a
    # standalone knowledge base it is noise, and the same block is already in
    # the split system prompt's INSTRUCTIONS section.
    tail = knowledge_base.rfind(_INPUT_VARIABLES_HEADER)
    if tail != -1:
        knowledge_base = knowledge_base[:tail]
    knowledge_base = knowledge_base.rstrip("\n") + "\n"

    return split_doc, knowledge_base


def section_word_counts(split_system_prompt_doc: str) -> dict[str, int]:
    """Return ``{"PERSONALITY": n, "GOAL": n, "INSTRUCTIONS": n}`` word counts.

    Splits only on the three top-level field headers — the INSTRUCTIONS body
    itself carries verbatim ``# CONVENTIONS`` / ``# SYSTEM CONSTANTS`` … H1 lines,
    so a generic H1 parse would wrongly treat those as separate sections.
    """
    counts: dict[str, int] = {}
    fields = ("PERSONALITY", "GOAL", "INSTRUCTIONS")
    for index, name in enumerate(fields):
        start = split_system_prompt_doc.index(f"# {name}\n") + len(f"# {name}\n")
        end = (
            split_system_prompt_doc.index(f"\n# {fields[index + 1]}\n", start)
            if index + 1 < len(fields)
            else len(split_system_prompt_doc)
        )
        counts[name] = len(split_system_prompt_doc[start:end].split())
    return counts
