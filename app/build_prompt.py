"""CLI entry point for the prompt compiler.

Usage:

    python app/build_prompt.py agents/defs/{agent_id} [options]

Writes diagnostic reports unconditionally (so authors can inspect them
on failure) and writes the System Prompt and Reference Asset artifacts
only when validation passes (and, when ``--fail-on-warnings`` is set,
only when there are no warnings either).

Default output layout::

    dist/{agent_id}/
        full/
            system_prompt.md            (subflow states always embedded)
            system_prompt_mini.md       (only if the agent's template has a
                                          *_mini.md.j2 companion on disk)
            reference_asset.md
            reference_asset.json
        split/                          (deploy-platform package)
            system_prompt.md            (# PERSONALITY / # GOAL / # INSTRUCTIONS)
            system_prompt_mini.md       (only if a mini prompt was rendered)
            knowledge_base.md           (the CONVERSATION_FLOW block + subflows)
            knowledge_base_mini.md      (only if a mini prompt was rendered)
            reference_asset.md
            reference_asset.json
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
from app.split_package import SPLIT_SYSTEM_PROMPT_WORD_LIMIT, section_word_counts


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


def _subflow_appendix(documents: dict[str, str]) -> str:
    """Return subflow documents as one trailing block, or ``""`` when empty.

    Subflow content is normally embedded directly in the rendered prompt
    (``embed_subflows=True``). If a caller opted out (``--split-subflows``),
    the documents are still folded into the single output file here rather
    than written as loose ``subflows/*.md`` — the flow must live in
    ``system_prompt.md`` / ``knowledge_base.md``, never in a side folder.
    """
    if not documents:
        return ""
    body = "\n\n".join(content.strip() for _, content in sorted(documents.items()))
    return "\n\n" + body + "\n"


def _write_reference_asset(outputs: CompilationOutputs, target: Path) -> list[Path]:
    """Write ``reference_asset.md`` / ``.json`` into ``target`` when present."""
    written: list[Path] = []
    if outputs.reference_asset_markdown is not None:
        ref_md_path = target / "reference_asset.md"
        ref_md_path.write_text(outputs.reference_asset_markdown, encoding="utf-8")
        written.append(ref_md_path)
    if outputs.reference_asset_json is not None:
        ref_json_path = target / "reference_asset.json"
        ref_json_path.write_text(
            json.dumps(outputs.reference_asset_json, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written.append(ref_json_path)
    return written


def _write_artifacts(outputs: CompilationOutputs, agent_dist: Path) -> list[Path]:
    """Write the ``full/`` and ``split/`` artifact bundles.

    ``full/`` holds the monolithic System Prompt (+ mini) and the Reference
    Asset. ``split/`` holds the deploy-platform package: a 3-section profile
    document (PERSONALITY / GOAL / INSTRUCTIONS), a standalone
    CONVERSATION_FLOW knowledge base (+ mini) and a copy of the Reference
    Asset. Subflow content is always folded into ``system_prompt.md`` /
    ``knowledge_base.md`` — never written as a side folder. Returns the list
    of written paths.
    """
    written: list[Path] = []

    subflows = _subflow_appendix(outputs.subflow_documents)
    subflows_mini = _subflow_appendix(outputs.subflow_documents_mini)

    full_dir = agent_dist / "full"
    full_dir.mkdir(parents=True, exist_ok=True)

    prompt_path = full_dir / "system_prompt.md"
    prompt_path.write_text(outputs.system_prompt + subflows, encoding="utf-8")
    written.append(prompt_path)

    if outputs.system_prompt_mini is not None:
        prompt_mini_path = full_dir / "system_prompt_mini.md"
        prompt_mini_path.write_text(
            outputs.system_prompt_mini + subflows_mini, encoding="utf-8"
        )
        written.append(prompt_mini_path)

    written += _write_reference_asset(outputs, full_dir)

    split_dir = agent_dist / "split"
    split_dir.mkdir(parents=True, exist_ok=True)

    split_prompt_path = split_dir / "system_prompt.md"
    split_prompt_path.write_text(outputs.split_system_prompt, encoding="utf-8")
    written.append(split_prompt_path)

    split_kb_path = split_dir / "knowledge_base.md"
    split_kb_path.write_text(
        outputs.split_knowledge_base + subflows, encoding="utf-8"
    )
    written.append(split_kb_path)

    if outputs.split_system_prompt_mini is not None:
        split_prompt_mini_path = split_dir / "system_prompt_mini.md"
        split_prompt_mini_path.write_text(
            outputs.split_system_prompt_mini, encoding="utf-8"
        )
        written.append(split_prompt_mini_path)

    if outputs.split_knowledge_base_mini is not None:
        split_kb_mini_path = split_dir / "knowledge_base_mini.md"
        split_kb_mini_path.write_text(
            outputs.split_knowledge_base_mini + subflows_mini, encoding="utf-8"
        )
        written.append(split_kb_mini_path)

    written += _write_reference_asset(outputs, split_dir)

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

    counts = section_word_counts(outputs.split_system_prompt)
    kb_words = len(outputs.split_knowledge_base.split())
    print(
        "Split package (words): "
        f"PERSONALITY {counts['PERSONALITY']} · GOAL {counts['GOAL']} · "
        f"INSTRUCTIONS {counts['INSTRUCTIONS']} · KB {kb_words}"
    )
    for name, count in counts.items():
        if count > SPLIT_SYSTEM_PROMPT_WORD_LIMIT:
            print(
                f"[!] Split package: {name} is {count} words, over the "
                f"{SPLIT_SYSTEM_PROMPT_WORD_LIMIT}-word profile-field limit."
            )

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
