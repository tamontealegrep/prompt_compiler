"""Unit tests for app/utils.py — regex extraction, namespacing, and alias resolution."""

from __future__ import annotations

import pytest

from app.utils import (
    dedupe_preserve_order,
    dynamic_target_slot_name,
    extract_assignment_slots,
    extract_constants,
    extract_goto_targets,
    extract_slots,
    extract_variables,
    has_unresolved_slot_alias,
    has_unresolved_state_alias_target,
    is_dynamic_target,
    namespace_slot_name,
    namespace_state_id,
    normalize_phrase,
    resolve_slot_aliases,
    resolve_state_alias_targets,
    resolve_state_alias_value,
    rewrite_local_goto_targets,
    rewrite_local_slots_in_text,
    substitute_params,
)

# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------


def test_extract_variables_finds_all_runtime_variables():
    text = "Hello {{caller_name}}, your city is {{city}}."
    assert extract_variables(text) == {"caller_name", "city"}


def test_extract_variables_ignores_constants_and_slots():
    text = "<CONST_NAME> and [slot_name] are not runtime variables."
    assert extract_variables(text) == set()


def test_extract_constants_finds_all_constants():
    text = "Use <MAX_RETRY_ATTEMPTS> and <DATA_LAW_REFERENCE>."
    assert extract_constants(text) == {"MAX_RETRY_ATTEMPTS", "DATA_LAW_REFERENCE"}


def test_extract_slots_finds_all_memory_slots():
    text = "Store into [age] and read from [city]."
    assert extract_slots(text) == {"age", "city"}


def test_extract_goto_targets_static_state_id():
    assert extract_goto_targets("IF [age] >= 18 GO_TO: ADULT_FLOW") == ["ADULT_FLOW"]


def test_extract_goto_targets_dynamic_slot():
    assert extract_goto_targets("GO_TO: [resume_state]") == ["[resume_state]"]


def test_extract_goto_targets_alias_token():
    assert extract_goto_targets("GO_TO: @callback.entry") == ["@callback.entry"]


def test_extract_goto_targets_returns_empty_for_no_match():
    assert extract_goto_targets("No GO_TO here.") == []


@pytest.mark.xfail(
    strict=True,
    reason="Known gap (SPEC.md B4): GO_TO detection is case-sensitive — a lowercase "
    "'go_to:' typo is silently invisible to extraction instead of being reported.",
)
def test_extract_goto_targets_is_case_insensitive():
    assert extract_goto_targets("go_to: SOME_STATE") == ["SOME_STATE"]


def test_extract_assignment_slots_direct_assignment():
    assert extract_assignment_slots("[age] = {{age_input}}") == {"age"}


def test_extract_assignment_slots_increment_form():
    assert extract_assignment_slots("increment [retry_count] by 1") == {"retry_count"}


def test_extract_assignment_slots_increment_is_case_insensitive():
    assert extract_assignment_slots("INCREMENT [retry_count] BY 1") == {"retry_count"}


def test_extract_assignment_slots_combines_both_forms():
    text = "[age] = {{age_input}}\nincrement [retry_count] by 1"
    assert extract_assignment_slots(text) == {"age", "retry_count"}


# ---------------------------------------------------------------------------
# Predicates
# ---------------------------------------------------------------------------


def test_is_dynamic_target_true_for_bracketed_slot():
    assert is_dynamic_target("[resume_state]") is True


def test_is_dynamic_target_false_for_static_id():
    assert is_dynamic_target("SOME_STATE") is False


def test_dynamic_target_slot_name_strips_brackets():
    assert dynamic_target_slot_name("[resume_state]") == "resume_state"


def test_has_unresolved_slot_alias_true_when_present():
    assert has_unresolved_slot_alias("Use @slot(callback.time) here.") is True


def test_has_unresolved_slot_alias_false_when_resolved():
    assert has_unresolved_slot_alias("Use [callback__time] here.") is False


def test_has_unresolved_state_alias_target_true_for_exact_single_space():
    assert has_unresolved_state_alias_target("GO_TO: @callback.entry") is True


def test_has_unresolved_state_alias_target_false_when_absent():
    assert has_unresolved_state_alias_target("GO_TO: CALLBACK__ENTRY") is False


@pytest.mark.xfail(
    strict=True,
    reason="Known gap (SPEC.md B4): the unresolved-alias guard is a substring check "
    "for the exact text 'GO_TO: @' (one space) and misses irregular whitespace.",
)
def test_has_unresolved_state_alias_target_detects_irregular_whitespace():
    assert has_unresolved_state_alias_target("GO_TO:  @callback.entry") is True


# ---------------------------------------------------------------------------
# Normalization and generic helpers
# ---------------------------------------------------------------------------


def test_normalize_phrase_lowercases_and_collapses_whitespace():
    assert normalize_phrase("  Hola   Mundo  ") == "hola mundo"


def test_dedupe_preserve_order_keeps_first_occurrence_order():
    assert dedupe_preserve_order(["b", "a", "b", "c", "a"]) == ["b", "a", "c"]


# ---------------------------------------------------------------------------
# Namespacing
# ---------------------------------------------------------------------------


def test_namespace_state_id_uppercases_namespace_and_uses_double_underscore():
    assert namespace_state_id("callback", "ASK_TIME") == "CALLBACK__ASK_TIME"


def test_namespace_slot_name_lowercases_namespace_and_uses_double_underscore():
    assert namespace_slot_name("Callback", "time") == "callback__time"


# ---------------------------------------------------------------------------
# Parameter and alias substitution
# ---------------------------------------------------------------------------


def test_substitute_params_replaces_all_occurrences():
    assert substitute_params("Hi <<name>>, bye <<name>>.", {"name": "Ana"}) == "Hi Ana, bye Ana."


def test_substitute_params_raises_value_error_for_missing_param():
    with pytest.raises(ValueError, match="agent_name"):
        substitute_params("Hi <<agent_name>>.", {})


def test_rewrite_local_slots_in_text_rewrites_mapped_slots_only():
    result = rewrite_local_slots_in_text(
        "[local_slot] and [global_slot]", {"local_slot": "ns__local_slot"}
    )
    assert result == "[ns__local_slot] and [global_slot]"


def test_rewrite_local_goto_targets_rewrites_mapped_static_targets_only():
    result = rewrite_local_goto_targets(
        "GO_TO: LOCAL_STATE", {"LOCAL_STATE": "NS__LOCAL_STATE"}
    )
    assert result == "GO_TO: NS__LOCAL_STATE"


def test_rewrite_local_goto_targets_leaves_unmapped_targets_untouched():
    result = rewrite_local_goto_targets("GO_TO: OTHER_STATE", {"LOCAL_STATE": "NS__LOCAL_STATE"})
    assert result == "GO_TO: OTHER_STATE"


def test_resolve_slot_aliases_replaces_with_bracketed_slot():
    result = resolve_slot_aliases(
        "Use @slot(callback.time) now.", {"callback.time": "callback__time"}
    )
    assert result == "Use [callback__time] now."


def test_resolve_slot_aliases_raises_for_unresolved_alias():
    with pytest.raises(ValueError, match=r"callback\.time"):
        resolve_slot_aliases("Use @slot(callback.time) now.", {})


def test_resolve_state_alias_targets_replaces_goto_alias():
    result = resolve_state_alias_targets(
        "GO_TO: @callback.entry", {"callback.entry": "CALLBACK__ENTRY"}
    )
    assert result == "GO_TO: CALLBACK__ENTRY"


def test_resolve_state_alias_targets_raises_for_unresolved_alias():
    with pytest.raises(ValueError, match=r"callback\.entry"):
        resolve_state_alias_targets("GO_TO: @callback.entry", {})


def test_resolve_state_alias_value_passes_through_non_alias():
    assert resolve_state_alias_value("SOME_STATE", {}) == "SOME_STATE"


def test_resolve_state_alias_value_resolves_alias():
    result = resolve_state_alias_value("@callback.entry", {"callback.entry": "CALLBACK__ENTRY"})
    assert result == "CALLBACK__ENTRY"


def test_resolve_state_alias_value_raises_for_unresolved_alias():
    with pytest.raises(ValueError, match=r"callback\.entry"):
        resolve_state_alias_value("@callback.entry", {})
