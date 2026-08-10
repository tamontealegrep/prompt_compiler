"""Integration test: the build_prompt.py CLI writes the mini artifact to disk.

Exercises app/build_prompt.py's _write_artifacts() directly (not through
sys.argv/main()) — this is the exact function whose file-writing this
session's mini-prompt work extended, and it's cheap to call without
shelling out to a subprocess.
"""

from __future__ import annotations

from app.build_prompt import _write_artifacts
from app.compiler import compile_agent
from app.schemas import ChannelType, CompilationParams
from tests.conftest import MINIMAL_AGENT_DIR


def test_write_artifacts_writes_system_prompt_mini(tmp_path):
    outputs = compile_agent(MINIMAL_AGENT_DIR, CompilationParams(channel=ChannelType.VOICE))
    written = _write_artifacts(outputs, tmp_path)

    mini_path = tmp_path / "system_prompt_mini.md"
    assert mini_path in written
    assert mini_path.read_text(encoding="utf-8") == outputs.system_prompt_mini


def test_write_artifacts_writes_system_prompt_and_mini_side_by_side(tmp_path):
    outputs = compile_agent(MINIMAL_AGENT_DIR, CompilationParams(channel=ChannelType.VOICE))
    _write_artifacts(outputs, tmp_path)

    assert (tmp_path / "system_prompt.md").exists()
    assert (tmp_path / "system_prompt_mini.md").exists()
    # The two artifacts encode the same information at different densities —
    # they must not be byte-identical (that would mean mini isn't doing
    # anything). Note: mini is NOT guaranteed to be smaller on every agent —
    # its fixed grammar-teaching overhead only amortizes on flows with
    # enough nodes (SPEC.md decision log, 2026-08-06: ~17% smaller on the
    # real 118-state reference agent, but larger on this 2-node fixture).
    full_text = (tmp_path / "system_prompt.md").read_text(encoding="utf-8")
    mini_text = (tmp_path / "system_prompt_mini.md").read_text(encoding="utf-8")
    assert full_text != mini_text
