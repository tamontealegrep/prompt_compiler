"""End-to-end compilation orchestrator.

This module exposes a single public function, :func:`compile_agent`, that
ties together every previous phase into one pipeline:

1. **Profiles** — load the channel profile (always) and, when requested,
   the compliance profile (:mod:`app.loaders`).
2. **AgentSpec assembly** — merge top-level YAMLs, shared includes and
   instantiated subflows; resolve all aliases (:mod:`app.loaders`).
3. **Classification** — split the spec into prompt-bound and
   reference-bound slices (:mod:`app.classifier`).
4. **Validation** — run the 11 graph validators plus, if a compliance
   profile is loaded, the registered compliance checkers
   (:mod:`app.validators`).
5. **Deduplication** — produce a textual-duplicate report that is
   informational only (:mod:`app.deduplicator`).
6. **Orphan-state diagnostic** — separate Markdown report
   (:mod:`app.validators.build_orphan_state_report`).
7. **Rendering** — System Prompt via Jinja2 and, when requested, the
   Reference Asset in Markdown and/or JSON (:mod:`app.renderers`).

The orchestrator never writes to disk — that is the CLI's job
(:mod:`build_prompt`). It returns a :class:`CompilationOutputs`
dataclass containing every artifact plus a :class:`CompilationStats`
summary so downstream code (CLI, tests, future API) can decide what to
do with the output.

Validation and rendering run **regardless of validator outcome**: a
report with errors still produces a System Prompt so that authors can
inspect it while debugging. The CLI is responsible for refusing to
write artifacts to disk when ``validation_report.has_errors()``.
"""

from __future__ import annotations

from pathlib import Path

from app.classifier import ContentClassifier
from app.deduplicator import find_duplicate_rules
from app.loaders import (
    load_agent_spec,
    load_channel_profile,
    load_compliance_profile,
)
from app.renderers import (
    render_all_subflow_documents,
    render_prompt,
    render_reference_asset_json,
    render_reference_asset_markdown,
)
from app.schemas import (
    CompilationOutputs,
    CompilationParams,
    CompilationStats,
    ReferenceAssetFormat,
)
from app.validators import build_orphan_state_report, validate_agent_spec


def _project_root() -> Path:
    """Return the project root directory (parent of ``app/``)."""
    return Path(__file__).resolve().parent.parent


def compile_agent(
    agent_dir: str | Path,
    params: CompilationParams | None = None,
) -> CompilationOutputs:
    """Compile an agent under ``agent_dir`` into a bundle of artifacts.

    Args:
        agent_dir: Path to the agent's configuration directory
            (``agents/defs/{agent_id}``).
        params: Compilation parameters. When ``None``, the SSOT defaults
            apply: voice channel, both reference asset formats enabled,
            no compliance profile.

    Returns:
        A :class:`CompilationOutputs` bundle. The validation report is
        always produced; the system prompt is always rendered. The
        reference asset Markdown / JSON are produced only when the
        corresponding flags in ``params`` are set.

    Raises:
        FileNotFoundError, NotADirectoryError, RuntimeError: propagated
        from :mod:`app.loaders` when the agent directory, profile YAMLs
        or referenced templates are missing or malformed.
    """
    if params is None:
        params = CompilationParams()

    # 1. Profiles.
    channel_profile = load_channel_profile(params.channel.value)
    compliance_profile = load_compliance_profile(params.compliance_profile)

    # 2. AgentSpec assembly.
    spec = load_agent_spec(agent_dir, params, channel_profile)

    # 3. Classification.
    classified = ContentClassifier().classify(spec)

    # 4-6. Diagnostics.
    validation_report = validate_agent_spec(spec, compliance_profile)
    dedup_report = find_duplicate_rules(spec, channel_profile)
    orphan_report = build_orphan_state_report(spec)

    # 7. Rendering.
    template_path = _project_root() / spec.manifest.template_path
    system_prompt = render_prompt(
        classified, template_path, channel_profile,
        embed_subflows=params.embed_subflows,
    )
    subflow_documents = (
        {}
        if params.embed_subflows
        else render_all_subflow_documents(spec)
    )

    reference_asset_markdown: str | None = None
    reference_asset_json: dict | None = None

    if params.include_reference_asset:
        if ReferenceAssetFormat.MARKDOWN in params.reference_asset_formats:
            reference_asset_markdown = render_reference_asset_markdown(classified)
        if ReferenceAssetFormat.JSON in params.reference_asset_formats:
            reference_asset_json = render_reference_asset_json(classified)

    # Compilation stats — character counts as a token-budget proxy.
    estimated_subflows_chars = sum(len(doc) for doc in subflow_documents.values())
    stats = CompilationStats(
        total_states=len(spec.states),
        total_handlers=len(spec.handlers),
        total_faqs=len(spec.faqs),
        total_subflows_instantiated=len(spec.manifest.subflow_instances),
        duplicate_rules_found=dedup_report.total_duplicates,
        estimated_system_prompt_chars=len(system_prompt),
        estimated_reference_asset_chars=len(reference_asset_markdown or ""),
        estimated_subflows_chars=estimated_subflows_chars,
    )

    return CompilationOutputs(
        agent_id=spec.manifest.agent_id,
        system_prompt=system_prompt,
        subflow_documents=subflow_documents,
        reference_asset_markdown=reference_asset_markdown,
        reference_asset_json=reference_asset_json,
        validation_report=validation_report,
        deduplication_report=dedup_report,
        orphan_report=orphan_report,
        stats=stats,
    )
