"""Unit tests for app/deduplicator.py — report-only duplicate-rule detection."""

from __future__ import annotations

from app.deduplicator import find_duplicate_rules
from app.schemas import ChannelProfile, ChannelType, PolicySectionDefinition
from tests.conftest import build_minimal_agent_spec, build_minimal_policies


def _channel_profile(*section_names: str) -> ChannelProfile:
    return ChannelProfile(
        channel=ChannelType.VOICE,
        display_name="Test Channel",
        policy_sections=[
            PolicySectionDefinition(name=name, label=name.upper(), required=False)
            for name in section_names
        ],
    )


def test_no_duplicates_in_a_clean_spec():
    spec = build_minimal_agent_spec(flow_rules=["Rule A", "Rule B"])
    report = find_duplicate_rules(spec, _channel_profile())
    assert report.has_duplicates() is False
    assert report.total_duplicates == 0


def test_duplicate_flow_rules_are_detected_case_and_whitespace_insensitively():
    spec = build_minimal_agent_spec(
        flow_rules=["Never rush the caller.", "  never   rush the caller.  "]
    )
    report = find_duplicate_rules(spec, _channel_profile())
    assert report.has_duplicates() is True
    assert report.total_duplicates == 1


def test_duplicate_across_flow_rules_and_faq_policy_is_detected():
    spec = build_minimal_agent_spec(
        flow_rules=["Always confirm the caller's identity."],
        faq_policy=["Always confirm the caller's identity."],
    )
    report = find_duplicate_rules(spec, _channel_profile())
    assert report.total_duplicates == 1
    group = report.duplicate_groups[0]
    locations = {occ.location for occ in group.occurrences}
    assert locations == {"flow_rules[1]", "faq_policy[1]"}


def test_duplicate_within_a_single_declared_policy_section_is_detected():
    profile = _channel_profile("compliance_and_scope_rules")
    spec = build_minimal_agent_spec(
        policies=build_minimal_policies(
            compliance_and_scope_rules=["No medical advice.", "No medical advice."]
        )
    )
    report = find_duplicate_rules(spec, profile)
    assert report.total_duplicates == 1


def test_policy_sections_not_declared_by_channel_profile_are_ignored():
    # The spec's policies has a section the channel profile doesn't declare;
    # the deduplicator must not choke on it (defensive branch per its docstring).
    spec = build_minimal_agent_spec(
        policies=build_minimal_policies(
            compliance_and_scope_rules=["Rule.", "Rule."]
        )
    )
    report = find_duplicate_rules(spec, _channel_profile("some_other_section"))
    assert report.total_duplicates == 0


def test_duplicate_groups_sorted_by_descending_occurrence_count():
    spec = build_minimal_agent_spec(
        flow_rules=["Rule A", "Rule A", "Rule B", "Rule B", "Rule B"]
    )
    report = find_duplicate_rules(spec, _channel_profile())
    assert [g.normalized_text for g in report.duplicate_groups] == ["rule b", "rule a"]


def test_total_duplicates_excludes_the_canonical_occurrence():
    spec = build_minimal_agent_spec(flow_rules=["Rule A", "Rule A", "Rule A"])
    report = find_duplicate_rules(spec, _channel_profile())
    assert report.total_duplicates == 2


def test_to_markdown_reports_no_duplicates_message_when_clean():
    spec = build_minimal_agent_spec(flow_rules=["Rule A"])
    report = find_duplicate_rules(spec, _channel_profile())
    assert "No duplicate rules found" in report.to_markdown()


def test_to_markdown_lists_each_occurrence_location():
    spec = build_minimal_agent_spec(flow_rules=["Rule A", "Rule A"])
    report = find_duplicate_rules(spec, _channel_profile())
    markdown = report.to_markdown()
    assert "flow_rules[1]" in markdown
    assert "flow_rules[2]" in markdown
