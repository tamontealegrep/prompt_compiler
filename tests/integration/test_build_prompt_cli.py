"""Integration test: the build_prompt.py CLI writes the full/ and split/ bundles.

Exercises app/build_prompt.py's _write_artifacts() directly (not through
sys.argv/main()) — cheap to call without shelling out to a subprocess.
"""

from __future__ import annotations

from app.build_prompt import _subflow_appendix, _write_artifacts
from app.compiler import compile_agent
from app.schemas import ChannelType, CompilationParams
from tests.conftest import MINIMAL_AGENT_DIR


def test_subflow_appendix_is_empty_when_no_documents():
    assert _subflow_appendix({}) == ""


def test_subflow_appendix_folds_documents_into_one_trailing_block():
    appendix = _subflow_appendix({"B": "# SUBFLOW: B\nb", "A": "# SUBFLOW: A\na"})
    # namespaces are emitted in sorted order, joined by a blank line
    assert appendix == "\n\n# SUBFLOW: A\na\n\n# SUBFLOW: B\nb\n"


def test_write_artifacts_writes_full_bundle(tmp_path):
    outputs = compile_agent(MINIMAL_AGENT_DIR, CompilationParams(channel=ChannelType.VOICE))
    _write_artifacts(outputs, tmp_path)

    assert (tmp_path / "full" / "system_prompt.md").read_text(encoding="utf-8") == (
        outputs.system_prompt
    )
    assert (tmp_path / "full" / "system_prompt_mini.md").read_text(encoding="utf-8") == (
        outputs.system_prompt_mini
    )
    assert (tmp_path / "full" / "reference_asset.md").exists()

    # Subflow content is always embedded — no side folder is written.
    assert not (tmp_path / "full" / "subflows").exists()
    assert not (tmp_path / "split" / "subflows").exists()

    # The two full-prompt artifacts encode the same information at different
    # densities — they must not be byte-identical.
    full_text = (tmp_path / "full" / "system_prompt.md").read_text(encoding="utf-8")
    mini_text = (tmp_path / "full" / "system_prompt_mini.md").read_text(encoding="utf-8")
    assert full_text != mini_text


def test_write_artifacts_writes_split_bundle(tmp_path):
    outputs = compile_agent(MINIMAL_AGENT_DIR, CompilationParams(channel=ChannelType.VOICE))
    written = _write_artifacts(outputs, tmp_path)

    split_prompt = tmp_path / "split" / "system_prompt.md"
    split_kb = tmp_path / "split" / "knowledge_base.md"
    assert split_prompt in written and split_kb in written
    assert split_prompt.read_text(encoding="utf-8").startswith("# PERSONALITY\n")
    assert split_kb.read_text(encoding="utf-8").startswith("# CONVERSATION_FLOW\n")
    assert (tmp_path / "split" / "system_prompt_mini.md").exists()
    assert (tmp_path / "split" / "knowledge_base_mini.md").exists()
    assert (tmp_path / "split" / "reference_asset.json").exists()
