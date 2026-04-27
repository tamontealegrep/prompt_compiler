"""Mermaid flowchart parser and SubflowTemplate scaffold generator.

Two public functions:

- :func:`parse_mermaid` reads a Mermaid ``flowchart TD`` or
  ``flowchart LR`` source and returns a :class:`MermaidParseResult`
  bundle of nodes, edges and subgraphs.
- :func:`scaffold_from_mermaid` writes one SubflowTemplate YAML per
  subgraph (and an extra YAML for top-level nodes) under ``output_dir``.
  The scaffolds are intentionally incomplete: the routing topology and
  state types are inferred from the diagram, but text fields (``say``,
  ``goal`` detail, ``capture``, etc.) require manual completion.

Supported Mermaid syntax:

================= =========================== ============
Syntax            Description                 Inferred type
================= =========================== ============
``ID[label]``     Rectangular node            ``message``
``ID{label}``     Diamond                     ``decision``
``ID([label])``   Stadium-shaped              ``terminal``
``ID[[label]]``   Double rectangle            ``action``
``A --> B``       Plain edge                  —
``A -- "x" --> B``  Edge with quoted label    —
``A -- x --> B``    Edge with unquoted label  —
``subgraph N[L]`` Subgraph open               —
``end``           Subgraph close              —
``direction TD``  Directive (ignored)         —
``%% comment``    Inline comment              —
================= =========================== ============

The parser is deliberately conservative: anything beyond this subset is
rejected silently — it produces no node and no edge for unrecognized
constructs. The compiler-level ``build_prompt.py`` is the source of
truth, not the scaffolder.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import yaml


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------


class MermaidNodeShape(str, Enum):
    """Shape of a Mermaid node, mapped to a flow object type by the scaffolder."""

    RECTANGULAR = "rectangular"
    DECISION = "decision"
    TERMINAL = "terminal"
    SUBROUTINE = "subroutine"
    START = "start"
    SUBFLOW_CHANGE = "subflow_change"


@dataclass(frozen=True, slots=True)
class MermaidNode:
    """A node parsed from the diagram."""

    node_id: str
    shape: MermaidNodeShape
    label: str


@dataclass(frozen=True, slots=True)
class MermaidEdge:
    """A directed edge with an optional condition label."""

    source_id: str
    target_id: str
    condition: Optional[str]


@dataclass(frozen=True, slots=True)
class MermaidSubgraph:
    """A subgraph block, named and labeled, listing the node ids it contains."""

    name: str
    label: str
    node_ids: list[str]


@dataclass(frozen=True, slots=True)
class MermaidParseResult:
    """Bundle returned by :func:`parse_mermaid`."""

    nodes: list[MermaidNode]
    edges: list[MermaidEdge]
    subgraphs: list[MermaidSubgraph]


# Type alias for inferred state types (mirrors schemas.NODE_TYPE).
NodeTypeLiteral = str


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------


_ID = r"[A-Za-z_][A-Za-z0-9_]*"

_NODE_DEF_RE = re.compile(
    rf"({_ID})"
    r"(?:"
    r"\[\[([^\]]+?)\]\]"     # group 2: [[label]] subroutine
    r"|\(\[([^\]]+?)\]\)"    # group 3: ([label]) terminal (stadium-shaped)
    r"|\(\(([^)]+?)\)\)"     # group 4: ((label)) start (circle)
    r"|\{\{([^}]+?)\}\}"     # group 5: {{label}} subflow_change (hexagon)
    r"|\{([^}]+?)\}"         # group 6: {label} decision
    r"|\[([^\]]+?)\]"        # group 7: [label] rectangular
    r")"
)

_EDGE_QUOTED_LABEL_RE = re.compile(
    rf"({_ID})\s+--\s+\"([^\"]+)\"\s+-->\s+({_ID})"
)
_EDGE_UNQUOTED_LABEL_RE = re.compile(
    rf"({_ID})\s+--\s+(.+?)\s+-->\s+({_ID})"
)
_EDGE_PLAIN_RE = re.compile(rf"({_ID})\s+-->\s+({_ID})")

_SUBGRAPH_OPEN_RE = re.compile(rf"^subgraph\s+({_ID})\s*(?:\[(.+?)\])?\s*$")
_HEADER_RE = re.compile(r"^flowchart\s+(TD|LR|TB|BT|RL)\s*$")

# YAML 1.1 truthy-strings that need explicit quoting in scaffold YAMLs.
_YAML_TRUTHY = {
    "yes", "no", "true", "false", "on", "off", "y", "n",
}


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _clean_label(label: str) -> str:
    """Normalize a Mermaid label: convert <br/>, collapse whitespace."""
    cleaned = label.replace("<br/>", " ").replace("<br>", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _extract_nodes_from_line(
    line: str, nodes_acc: dict[str, tuple[MermaidNodeShape, str]]
) -> str:
    """Pull node definitions out of ``line`` and replace them with bare ids.

    The bare-id form is what the edge parsers operate on. ``nodes_acc`` is
    updated in place so the latest definition wins (Mermaid allows
    redefinition; in practice the diagrams we parse are consistent).
    """

    def repl(match: re.Match[str]) -> str:
        node_id = match.group(1)
        if match.group(2) is not None:
            shape = MermaidNodeShape.SUBROUTINE
            label = match.group(2)
        elif match.group(3) is not None:
            shape = MermaidNodeShape.TERMINAL
            label = match.group(3)
        elif match.group(4) is not None:
            shape = MermaidNodeShape.START
            label = match.group(4)
        elif match.group(5) is not None:
            shape = MermaidNodeShape.SUBFLOW_CHANGE
            label = match.group(5)
        elif match.group(6) is not None:
            shape = MermaidNodeShape.DECISION
            label = match.group(6)
        else:
            shape = MermaidNodeShape.RECTANGULAR
            label = match.group(7)
        nodes_acc[node_id] = (shape, _clean_label(label))
        return node_id

    return _NODE_DEF_RE.sub(repl, line)


def _parse_edge(line: str) -> Optional[MermaidEdge]:
    """Try the three edge patterns in order; return ``None`` when none match."""
    m = _EDGE_QUOTED_LABEL_RE.search(line)
    if m:
        return MermaidEdge(
            source_id=m.group(1),
            target_id=m.group(3),
            condition=m.group(2).strip(),
        )

    m = _EDGE_UNQUOTED_LABEL_RE.search(line)
    if m:
        condition = m.group(2).strip()
        # Sanity: the unquoted-label regex is greedy. If we accidentally
        # captured another arrow, fall through to the plain pattern.
        if "-->" not in condition and "--" not in condition:
            return MermaidEdge(
                source_id=m.group(1),
                target_id=m.group(3),
                condition=condition,
            )

    m = _EDGE_PLAIN_RE.search(line)
    if m:
        return MermaidEdge(
            source_id=m.group(1),
            target_id=m.group(2),
            condition=None,
        )

    return None


def parse_mermaid(source: str) -> MermaidParseResult:
    """Parse a Mermaid flowchart source into a :class:`MermaidParseResult`."""
    nodes_acc: dict[str, tuple[MermaidNodeShape, str]] = {}
    edges: list[MermaidEdge] = []
    subgraphs: list[MermaidSubgraph] = []

    current_subgraph: Optional[dict[str, Any]] = None
    in_flowchart = False

    for raw_line in source.splitlines():
        line = raw_line.split("%%")[0].strip()
        if not line:
            continue

        if not in_flowchart:
            if _HEADER_RE.match(line):
                in_flowchart = True
            continue

        if line == "end":
            if current_subgraph is not None:
                subgraphs.append(
                    MermaidSubgraph(
                        name=current_subgraph["name"],
                        label=current_subgraph["label"],
                        node_ids=list(current_subgraph["node_ids"]),
                    )
                )
                current_subgraph = None
            continue

        m = _SUBGRAPH_OPEN_RE.match(line)
        if m:
            current_subgraph = {
                "name": m.group(1),
                "label": _clean_label(m.group(2) or m.group(1)),
                "node_ids": [],
            }
            continue

        if line.startswith("direction "):
            continue

        stripped = _extract_nodes_from_line(line, nodes_acc)

        # Track which ids appear in this line so the surrounding subgraph
        # (if any) can record membership.
        line_ids = [
            tok for tok in re.findall(rf"\b({_ID})\b", stripped)
            if tok not in {"end", "subgraph", "direction"}
        ]
        if current_subgraph is not None:
            for nid in line_ids:
                if nid not in current_subgraph["node_ids"]:
                    current_subgraph["node_ids"].append(nid)

        edge = _parse_edge(stripped)
        if edge is not None:
            edges.append(edge)

    nodes = [
        MermaidNode(node_id=nid, shape=shape, label=label)
        for nid, (shape, label) in nodes_acc.items()
    ]
    return MermaidParseResult(nodes=nodes, edges=edges, subgraphs=subgraphs)


# ---------------------------------------------------------------------------
# Scaffold generator
# ---------------------------------------------------------------------------


def _infer_node_type(
    shape: MermaidNodeShape, has_outgoing_edges: bool
) -> NodeTypeLiteral:
    """Map a Mermaid shape + outgoing-edge presence to a flow-object type."""
    if shape == MermaidNodeShape.TERMINAL:
        return "terminal"
    if shape == MermaidNodeShape.START:
        return "start"
    if shape == MermaidNodeShape.SUBFLOW_CHANGE:
        return "subflow_change"
    if shape == MermaidNodeShape.DECISION:
        return "decision"
    if shape == MermaidNodeShape.SUBROUTINE:
        return "action"
    if not has_outgoing_edges:
        return "terminal"
    return "message"


def _to_state_id(mermaid_id: str) -> str:
    """Normalize a Mermaid id to a valid state_id (UPPER_SNAKE_CASE).

    - ``A``       → ``A``
    - ``nodeA``   → ``NODE_A``
    - ``node_1``  → ``NODE_1``
    - ``1A``      → ``S_1A``  (state ids must start with an uppercase letter)
    """
    # Insert underscore only at lowercase-or-digit → uppercase boundaries so
    # already-snake-cased ids like ``S_HELLO`` pass through untouched.
    snake = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", mermaid_id)
    upper = snake.upper()
    cleaned = re.sub(r"[^A-Z0-9_]", "_", upper)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        cleaned = "S_UNNAMED"
    if not cleaned[0].isalpha():
        cleaned = "S_" + cleaned
    return cleaned


def _build_state_dict(
    node: MermaidNode,
    edges_from_node: list[MermaidEdge],
    id_to_state_id: dict[str, str],
) -> dict[str, Any]:
    """Build the YAML-ready dict for a single state from a Mermaid node."""
    state_id = id_to_state_id[node.node_id]
    has_outgoing = bool(edges_from_node)
    node_type = _infer_node_type(node.shape, has_outgoing)

    state: dict[str, Any] = {
        "state_id": state_id,
        "type": node_type,
        "goal": [
            f'[scaffold] Inferred from Mermaid label: "{node.label}"',
        ],
        "do": [],
    }

    # SAY block — placeholder text seeded from the Mermaid label so the
    # scaffold validates against the schema (message/question require a
    # non-empty SAY) and so the author can grep for ``[scaffold]`` markers
    # to find lines that still need editing. The decision type forbids
    # SAY entirely; we omit it.
    placeholder_say = (
        f'[scaffold] TODO: replace with user-facing text. '
        f'Mermaid label: "{node.label}"'
    )
    if node_type in {"message", "question", "action", "terminal"}:
        state["say"] = [placeholder_say]
        state["say_verbatim"] = False

    # WAIT semantics depend on node type. We always emit a string so PyYAML
    # 1.1 doesn't coerce yes/no into booleans on reload.
    if node_type == "question":
        state["wait"] = "yes"
    elif node_type == "decision":
        # decision nodes don't strictly need a wait; omit so the schema's
        # default (None) applies.
        pass
    else:
        state["wait"] = "no"

    if node_type == "action":
        # ``execute`` must match LOWER_SNAKE_RE; ``todo_tool_name`` is a
        # valid placeholder that the author replaces with a real tool id.
        state["execute"] = "todo_tool_name"

    state["capture"] = []
    state["store"] = []

    routes: list[str] = []
    for edge in edges_from_node:
        target = id_to_state_id.get(edge.target_id, edge.target_id)
        if edge.condition:
            routes.append(f'IF "{edge.condition}" -> GO_TO: {target}')
        else:
            routes.append(f"always GO_TO: {target}")
    state["route"] = routes
    state["fallback"] = []

    if node_type == "terminal":
        state["final"] = "yes"

    return state


def _build_subflow_template(
    template_id: str,
    description: str,
    nodes: list[MermaidNode],
    edges: list[MermaidEdge],
    id_to_state_id: dict[str, str],
) -> dict[str, Any]:
    """Build a SubflowTemplate-shaped dict for the supplied nodes and edges."""
    edges_by_source: dict[str, list[MermaidEdge]] = {}
    for edge in edges:
        edges_by_source.setdefault(edge.source_id, []).append(edge)

    states: list[dict[str, Any]] = []
    terminals: list[dict[str, Any]] = []

    for node in nodes:
        outgoing = edges_by_source.get(node.node_id, [])
        state = _build_state_dict(node, outgoing, id_to_state_id)
        if state["type"] == "terminal":
            terminals.append(state)
        else:
            states.append(state)

    template: dict[str, Any] = {
        "template_id": template_id,
        "description": description,
        "params": [],
        "required_tools": [],
        "local_memory_slots": [],
        "flow_rules": [],
        "faq_policy": [],
        "handlers": [],
        "faqs": [],
        "states": states,
        "terminal_states": terminals,
        "exports": {"states": {}, "slots": {}},
    }
    return template


# Custom YAML dumper that quotes booleans-as-strings (yes/no/true/false…).
class _ScaffoldDumper(yaml.SafeDumper):
    """Dumper that quotes YAML 1.1 truthy strings to avoid bool coercion."""


def _str_representer(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    if data.lower() in _YAML_TRUTHY:
        return dumper.represent_scalar(
            "tag:yaml.org,2002:str", data, style='"'
        )
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_ScaffoldDumper.add_representer(str, _str_representer)


def _write_yaml(path: Path, content: dict[str, Any]) -> None:
    """Write ``content`` as a YAML file using :class:`_ScaffoldDumper`."""
    text = yaml.dump(
        content,
        Dumper=_ScaffoldDumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    path.write_text(text, encoding="utf-8")


def scaffold_from_mermaid(
    result: MermaidParseResult,
    output_dir: Path,
    *,
    agent_id: str,
    split_subgraphs: bool = True,
) -> list[Path]:
    """Write SubflowTemplate YAML scaffolds to ``output_dir``.

    Args:
        result: Parsed Mermaid bundle.
        output_dir: Target directory; created when missing.
        agent_id: Used as the template_id for the "main" file (top-level
            nodes that are not inside any subgraph).
        split_subgraphs: When ``True`` and at least one subgraph exists,
            each subgraph becomes its own file. When ``False`` or no
            subgraphs exist, every node lands in a single file named
            after ``agent_id``.

    Returns:
        The list of files written (in stable order).
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    id_to_state_id = {n.node_id: _to_state_id(n.node_id) for n in result.nodes}

    written: list[Path] = []

    if split_subgraphs and result.subgraphs:
        # Emit one file per subgraph plus an extra "main" file for nodes
        # that fall outside every subgraph.
        node_by_id = {n.node_id: n for n in result.nodes}
        all_subgraph_node_ids: set[str] = set()
        for sg in result.subgraphs:
            sg_node_ids = [nid for nid in sg.node_ids if nid in node_by_id]
            sg_nodes = [node_by_id[nid] for nid in sg_node_ids]
            sg_node_id_set = set(sg_node_ids)
            sg_edges = [
                e
                for e in result.edges
                if e.source_id in sg_node_id_set and e.target_id in sg_node_id_set
            ]
            template_id = sg.name.lower()
            template = _build_subflow_template(
                template_id=template_id,
                description=f"[scaffold] Generated from Mermaid subgraph '{sg.name}'.",
                nodes=sg_nodes,
                edges=sg_edges,
                id_to_state_id=id_to_state_id,
            )
            path = output_dir / f"{template_id}.yaml"
            _write_yaml(path, template)
            written.append(path)
            all_subgraph_node_ids.update(sg_node_ids)

        top_level_nodes = [
            n for n in result.nodes if n.node_id not in all_subgraph_node_ids
        ]
        if top_level_nodes:
            top_node_ids = {n.node_id for n in top_level_nodes}
            top_edges = [
                e
                for e in result.edges
                if e.source_id in top_node_ids or e.target_id in top_node_ids
            ]
            template = _build_subflow_template(
                template_id=agent_id,
                description=f"[scaffold] Generated main subflow for '{agent_id}'.",
                nodes=top_level_nodes,
                edges=top_edges,
                id_to_state_id=id_to_state_id,
            )
            path = output_dir / f"{agent_id}.yaml"
            _write_yaml(path, template)
            written.append(path)
    else:
        # Single file containing everything.
        template = _build_subflow_template(
            template_id=agent_id,
            description=f"[scaffold] Generated subflow for '{agent_id}'.",
            nodes=result.nodes,
            edges=result.edges,
            id_to_state_id=id_to_state_id,
        )
        path = output_dir / f"{agent_id}.yaml"
        _write_yaml(path, template)
        written.append(path)

    return written