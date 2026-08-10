"""CLI entry point for the prompt compiler.

Usage:

    python app/build_prompt.py agents/defs/{agent_id} [options]

Writes diagnostic reports unconditionally (so authors can inspect them
on failure) and writes the System Prompt and Reference Asset artifacts
only when validation passes (and, when ``--fail-on-warnings`` is set,
only when there are no warnings either).

Default output layout::

    dist/{agent_id}/
        system_prompt.md
        system_prompt_mini.md      (only if the agent's template has a
                                     *_mini.md.j2 companion on disk)
        reference_asset.md
        reference_asset.json
        subflows/
            {NAMESPACE}.md   (one file per subflow)
        subflows_mini/
            {NAMESPACE}.md   (only alongside system_prompt_mini.md)
        reports/
            validation_report.md
            deduplication_report.md
            orphan_states_report.md
        diagrams/
            (populated by the diagram generator in main.py)

Exit codes:

- ``0`` — build completed successfully, all artifacts written.
- ``1`` — fatal error (missing directory, malformed YAML, validation
  errors, or warnings under ``--fail-on-warnings``). Prompt and
  reference asset are NOT written. Diagnostic reports ARE written.
- ``2`` — argparse error (default for invalid CLI usage).
"""

from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))  # project root → app.* imports work

import argparse
import json
import sys
from pathlib import Path

from app.compiler import compile_agent
from app.schemas import (
    ChannelType,
    CompilationOutputs,
    CompilationParams,
    ReferenceAssetFormat,
    VerbosityLevel,
)


def _build_parser() -> argparse.ArgumentParser:
    """Return the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="build_prompt",
        description="Build and validate a modular state-machine system prompt.",
    )
    parser.add_argument(
        "agent_dir",
        help="Agent definitions directory (e.g. agents/defs/bot_voice).",
    )
    parser.add_argument(
        "--channel",
        choices=[c.value for c in ChannelType],
        default=ChannelType.VOICE.value,
        help="Compilation channel (default: voice).",
    )
    parser.add_argument(
        "--max-prompt-tokens",
        type=int,
        default=None,
        help="Soft token budget — informational only in v2.",
    )
    parser.add_argument(
        "--verbosity",
        choices=[v.value for v in VerbosityLevel],
        default=VerbosityLevel.STANDARD.value,
        help="Verbosity of auxiliary context in the system prompt.",
    )
    parser.add_argument(
        "--no-reference-asset",
        action="store_true",
        help="Skip Reference Asset rendering entirely.",
    )
    parser.add_argument(
        "--reference-formats",
        nargs="+",
        choices=[f.value for f in ReferenceAssetFormat],
        default=[ReferenceAssetFormat.MARKDOWN.value, ReferenceAssetFormat.JSON.value],
        help="Reference Asset formats to produce (default: markdown json).",
    )
    parser.add_argument(
        "--compliance-profile",
        default=None,
        help="Compliance profile id (e.g. medical_es). None disables compliance checks.",
    )
    parser.add_argument(
        "--fail-on-warnings",
        action="store_true",
        help="Treat validator warnings as build errors.",
    )
    parser.add_argument(
        "--split-subflows",
        action="store_true",
        help=(
            "Write subflow states as separate subflows/ reference documents "
            "instead of embedding them inline in system_prompt.md.  By default "
            "all states are embedded in a single file."
        ),
    )
    parser.add_argument(
        "--dist-dir",
        default="dist",
        help="Output root directory (default: dist).",
    )
    return parser


def _params_from_args(args: argparse.Namespace) -> CompilationParams:
    """Map CLI args onto a :class:`CompilationParams`."""
    return CompilationParams(
        channel=ChannelType(args.channel),
        max_prompt_tokens=args.max_prompt_tokens,
        verbosity=VerbosityLevel(args.verbosity),
        include_reference_asset=not args.no_reference_asset,
        reference_asset_formats=[
            ReferenceAssetFormat(f) for f in args.reference_formats
        ],
        compliance_profile=args.compliance_profile,
        embed_subflows=not args.split_subflows,
    )


def _resolve_dist_root(dist_dir_arg: str) -> Path:
    """Resolve ``--dist-dir`` either as absolute or relative to the project root."""
    candidate = Path(dist_dir_arg)
    if candidate.is_absolute():
        return candidate
    return Path(__file__).resolve().parent.parent / candidate


def _write_diagnostics(outputs: CompilationOutputs, agent_dist: Path) -> None:
    """Write validation, deduplication and orphan reports into reports/ subfolder."""
    reports_dir = agent_dist / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "validation_report.md").write_text(
        outputs.validation_report.to_markdown(), encoding="utf-8"
    )
    (reports_dir / "deduplication_report.md").write_text(
        outputs.deduplication_report.to_markdown(), encoding="utf-8"
    )
    (reports_dir / "orphan_states_report.md").write_text(
        outputs.orphan_report, encoding="utf-8"
    )


def _write_artifacts(outputs: CompilationOutputs, agent_dist: Path) -> list[Path]:
    """Write the System Prompt, subflow documents, and Reference Asset.

    Returns the list of written paths.
    """
    written: list[Path] = []

    prompt_path = agent_dist / "system_prompt.md"
    prompt_path.write_text(outputs.system_prompt, encoding="utf-8")
    written.append(prompt_path)

    if outputs.subflow_documents:
        subflows_dir = agent_dist / "subflows"
        subflows_dir.mkdir(parents=True, exist_ok=True)
        for namespace, content in sorted(outputs.subflow_documents.items()):
            sf_path = subflows_dir / f"{namespace}.md"
            sf_path.write_text(content, encoding="utf-8")
            written.append(sf_path)

    if outputs.system_prompt_mini is not None:
        prompt_mini_path = agent_dist / "system_prompt_mini.md"
        prompt_mini_path.write_text(outputs.system_prompt_mini, encoding="utf-8")
        written.append(prompt_mini_path)

        if outputs.subflow_documents_mini:
            subflows_mini_dir = agent_dist / "subflows_mini"
            subflows_mini_dir.mkdir(parents=True, exist_ok=True)
            for namespace, content in sorted(outputs.subflow_documents_mini.items()):
                sf_mini_path = subflows_mini_dir / f"{namespace}.md"
                sf_mini_path.write_text(content, encoding="utf-8")
                written.append(sf_mini_path)

    if outputs.reference_asset_markdown is not None:
        ref_md_path = agent_dist / "reference_asset.md"
        ref_md_path.write_text(outputs.reference_asset_markdown, encoding="utf-8")
        written.append(ref_md_path)

    if outputs.reference_asset_json is not None:
        ref_json_path = agent_dist / "reference_asset.json"
        ref_json_path.write_text(
            json.dumps(outputs.reference_asset_json, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written.append(ref_json_path)

    return written


def _print_summary(outputs: CompilationOutputs) -> None:
    """Print the per-build human summary to stdout."""
    s = outputs.stats
    print()
    print(f"Agent: {outputs.agent_id}")
    print(
        f"States: {s.total_states} · Handlers: {s.total_handlers} · "
        f"FAQs: {s.total_faqs} · Subflow instances: {s.total_subflows_instantiated}"
    )
    print(f"System Prompt chars: {s.estimated_system_prompt_chars}")
    n_subflows = len(outputs.subflow_documents)
    print(
        f"Subflow documents: {n_subflows} ({s.estimated_subflows_chars} chars total)"
    )
    if s.estimated_system_prompt_mini_chars is not None:
        total_full = s.estimated_system_prompt_chars + s.estimated_subflows_chars
        total_mini = s.estimated_system_prompt_mini_chars + s.estimated_subflows_mini_chars
        reduction = round(100 * (1 - total_mini / total_full)) if total_full else 0
        print(
            f"System Prompt (mini) chars: {s.estimated_system_prompt_mini_chars} "
            f"+ {s.estimated_subflows_mini_chars} subflow ({reduction}% smaller than full)"
        )
    print(f"Reference Asset chars: {s.estimated_reference_asset_chars}")
    print(f"Duplicate rules: {s.duplicate_rules_found}")
    n_err = len(outputs.validation_report.errors)
    n_warn = len(outputs.validation_report.warnings)
    print(f"Validation: {n_err} errors, {n_warn} warnings")


def main(argv: list[str] | None = None) -> int:
    """Run the CLI. Return the process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    params = _params_from_args(args)

    try:
        outputs = compile_agent(args.agent_dir, params)
    except (FileNotFoundError, NotADirectoryError, RuntimeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    dist_root = _resolve_dist_root(args.dist_dir)
    agent_dist = dist_root / outputs.agent_id
    agent_dist.mkdir(parents=True, exist_ok=True)

    # Always write diagnostics so authors can inspect them on failure.
    _write_diagnostics(outputs, agent_dist)

    _print_summary(outputs)
    print(f"Reports: {agent_dist / 'reports'}/")

    if outputs.validation_report.has_errors():
        print(file=sys.stderr)
        print(
            "Build aborted due to validation errors. "
            "See validation_report.md for details.",
            file=sys.stderr,
        )
        return 1

    if args.fail_on_warnings and outputs.validation_report.has_warnings():
        print(file=sys.stderr)
        print(
            "Build aborted because --fail-on-warnings is set and the build "
            "produced warnings. See validation_report.md for details.",
            file=sys.stderr,
        )
        return 1

    # Validation passed. Write the System Prompt and Reference Asset.
    written = _write_artifacts(outputs, agent_dist)

    print()
    for path in written:
        print(f"Wrote: {path}")
    print()
    print("Build completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
