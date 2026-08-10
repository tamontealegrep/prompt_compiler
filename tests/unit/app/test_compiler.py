"""Unit tests for app/compiler.py — the compile_agent() orchestrator.

Most of compile_agent()'s behavior is already exercised end-to-end by
tests/integration/. This module covers compiler.py-specific logic that
isn't a good fit for those: the mini-template path derivation and the
graceful skip when an agent's template has no compact-notation companion.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from app.compiler import _mini_template_path, compile_agent
from app.schemas import ChannelType, CompilationParams
from tests.conftest import MINIMAL_AGENT_DIR

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BILINGUAL_TEMPLATE = PROJECT_ROOT / "templates" / "system_prompt_bilingual_text.md.j2"


def test_mini_template_path_naming_convention():
    assert _mini_template_path(Path("templates/system_prompt.md.j2")) == Path(
        "templates/system_prompt_mini.md.j2"
    )


def test_mini_template_path_preserves_directory():
    result = _mini_template_path(Path("agents/defs/x/templates/system_prompt.md.j2"))
    assert result == Path("agents/defs/x/templates/system_prompt_mini.md.j2")


def test_compile_agent_renders_mini_prompt_when_companion_template_exists():
    outputs = compile_agent(MINIMAL_AGENT_DIR, CompilationParams(channel=ChannelType.VOICE))
    assert outputs.system_prompt_mini is not None
    assert "MSG GREETING" in outputs.system_prompt_mini
    assert outputs.stats.estimated_system_prompt_mini_chars == len(outputs.system_prompt_mini)


def test_compile_agent_skips_mini_prompt_when_no_companion_template_exists(tmp_path):
    # The bilingual template intentionally has no *_mini.md.j2 companion
    # (SPEC.md decision log, 2026-08-06) — this exercises the real,
    # currently-shipping case where mini rendering must be skipped, not
    # a synthetic one.
    assert BILINGUAL_TEMPLATE.exists()
    mini_companion = _mini_template_path(BILINGUAL_TEMPLATE)
    assert not mini_companion.exists()

    agent_dir = tmp_path / "agent_without_mini"
    shutil.copytree(MINIMAL_AGENT_DIR, agent_dir)
    manifest_path = agent_dir / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["template_path"] = "templates/system_prompt_bilingual_text.md.j2"
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")

    outputs = compile_agent(agent_dir, CompilationParams(channel=ChannelType.VOICE))

    assert outputs.system_prompt is not None  # the full prompt still renders
    assert outputs.system_prompt_mini is None
    assert outputs.subflow_documents_mini == {}
    assert outputs.stats.estimated_system_prompt_mini_chars is None


def test_compile_agent_mini_respects_embed_subflows_false():
    outputs = compile_agent(
        MINIMAL_AGENT_DIR,
        CompilationParams(channel=ChannelType.VOICE, embed_subflows=False),
    )
    # The fixture agent has no subflow instances, so this is an empty dict
    # either way — the assertion is that the *type* is right and rendering
    # didn't raise when embed_subflows=False is combined with mini rendering.
    assert outputs.subflow_documents_mini == {}
    assert outputs.system_prompt_mini is not None
