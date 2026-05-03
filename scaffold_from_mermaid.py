"""CLI that parses a Mermaid flowchart and writes SubflowTemplate scaffolds.

Usage:

    python scaffold_from_mermaid.py <mermaid_file> <output_dir> --agent-id ID

The Mermaid input must be a ``flowchart TD`` or ``flowchart LR`` source.
For each ``subgraph`` block one YAML scaffold is written; remaining
top-level nodes get an extra YAML named after ``--agent-id``.

The scaffolds satisfy the SubflowTemplate schema but contain placeholder
text in ``goal`` and empty ``say`` blocks — they are starting points for
manual completion, not finished flows.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.mermaid_parser import parse_mermaid, scaffold_from_mermaid


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scaffold_from_mermaid",
        description="Generate YAML SubflowTemplate scaffolds from a Mermaid flowchart.",
    )
    parser.add_argument(
        "mermaid_file",
        help="Path to a .mmd or .md file containing a flowchart TD/LR diagram.",
    )
    parser.add_argument(
        "output_dir",
        help="Directory to write the YAML scaffolds (created if missing).",
    )
    parser.add_argument(
        "--agent-id",
        required=True,
        help="Agent id used for the 'main' file when there are top-level nodes "
        "outside every subgraph.",
    )
    parser.add_argument(
        "--no-split-subgraphs",
        action="store_true",
        help="Place every node in a single file instead of one file per subgraph.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the scaffolder. Return the process exit code."""
    args = _build_parser().parse_args(argv)

    source_path = Path(args.mermaid_file)
    if not source_path.exists():
        print(f"[ERROR] No existe el archivo Mermaid: {source_path}", file=sys.stderr)
        return 1

    source = source_path.read_text(encoding="utf-8")
    result = parse_mermaid(source)

    if not result.nodes:
        print(
            "[ERROR] No se detectaron nodos. ¿La fuente comienza con "
            "'flowchart TD' o 'flowchart LR'?",
            file=sys.stderr,
        )
        return 1

    output_dir = Path(args.output_dir)
    written = scaffold_from_mermaid(
        result,
        output_dir,
        agent_id=args.agent_id,
        split_subgraphs=not args.no_split_subgraphs,
    )

    print(
        f"Parsed {len(result.nodes)} nodes, {len(result.edges)} edges, "
        f"{len(result.subgraphs)} subgraphs."
    )
    print(f"Wrote {len(written)} scaffold file(s):")
    for path in written:
        print(f"  {path}")
    print()
    print(
        "These scaffolds need manual completion: fill in `say`, `goal`, "
        "`capture`, etc. Then reference them from your agent manifest."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())