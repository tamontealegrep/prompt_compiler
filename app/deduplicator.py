"""Detection of textually-duplicate rules across flow_rules, faq_policy and policies.

Two rules are considered duplicate when their normalized form (lowercase
plus whitespace collapsed via :func:`app.utils.normalize_phrase`) is
identical. The deduplicator inspects:

- ``spec.flow_rules``
- ``spec.faq_policy``
- Every section of ``spec.policies`` that the active ``ChannelProfile``
  declares.

It is **report-only**: the ``AgentSpec`` is never modified. Authors review
the report and decide which copy to keep — the compiler does not silently
collapse rules to avoid losing semantic distinctions that are invisible to
textual normalization.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.utils import normalize_phrase

if TYPE_CHECKING:
    from app.schemas import AgentSpec, ChannelProfile


@dataclass(frozen=True, slots=True)
class DuplicateOccurrence:
    """One location where a duplicate rule appears.

    ``location`` is a structured path (e.g. ``"flow_rules[3]"`` or
    ``"policies.compliance_and_scope_rules[2]"``). ``original_text`` is
    the verbatim text of that occurrence (preserving the original
    capitalization and whitespace) so authors can find it with a simple
    grep against their YAML files.
    """

    location: str
    original_text: str


@dataclass(frozen=True, slots=True)
class DuplicateRuleGroup:
    """A set of locations whose rules share the same normalized text."""

    normalized_text: str
    occurrences: list[DuplicateOccurrence]


@dataclass
class DeduplicationReport:
    """Outcome of running the deduplicator on an ``AgentSpec``.

    Mutable by design (the build code accumulates groups incrementally).
    Consumers — primarily the CLI and the compiler — read it but should
    not mutate it after :func:`find_duplicate_rules` returns.
    """

    duplicate_groups: list[DuplicateRuleGroup] = field(default_factory=list)

    @property
    def total_duplicates(self) -> int:
        """Total number of redundant occurrences.

        Excludes the canonical occurrence per group: a group of three
        identical lines has two redundant occurrences, not three.
        """
        return sum(len(g.occurrences) - 1 for g in self.duplicate_groups)

    def has_duplicates(self) -> bool:
        """Return ``True`` if any duplicate group exists."""
        return bool(self.duplicate_groups)

    def to_markdown(self) -> str:
        """Render the report as Markdown.

        The format is intentionally conservative — every duplicate group
        becomes one ``##`` section, each occurrence a list item — so the
        artifact diffs cleanly between builds.
        """
        if not self.duplicate_groups:
            return (
                "# Deduplication Report\n"
                "\n"
                "- No duplicate rules found.\n"
            )

        lines = [
            "# Deduplication Report",
            "",
            (
                f"- Total redundant occurrences: {self.total_duplicates} "
                f"(across {len(self.duplicate_groups)} groups)"
            ),
            "",
        ]
        for idx, group in enumerate(self.duplicate_groups, start=1):
            lines.extend(
                [
                    f"## Duplicate group {idx}",
                    "",
                    f"**Normalized text**: `{group.normalized_text}`",
                    "",
                    "**Occurrences**:",
                ]
            )
            for occ in group.occurrences:
                # Escape backticks in the original text by replacing them
                # with single quotes inside the inline string. We keep
                # the structure simple and predictable for diffing.
                escaped = occ.original_text.replace('"', '\\"')
                lines.append(f'- `{occ.location}`: "{escaped}"')
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def find_duplicate_rules(
    spec: "AgentSpec",
    channel_profile: "ChannelProfile",
) -> DeduplicationReport:
    """Find textually-duplicate rules across flow_rules, faq_policy and policies.

    The active ``channel_profile`` is required so the deduplicator only
    considers policy sections valid for the agent's compilation channel.
    Sections that exist in ``spec.policies`` but are not declared by the
    channel profile are ignored — but the merge step in
    :mod:`app.loaders` already rejects those, so this branch is mostly
    defensive.

    The result is sorted by descending occurrence count, then by
    normalized text, so iterating the report produces a stable order
    across runs (useful for diffing build artifacts in CI).
    """
    buckets: dict[str, list[DuplicateOccurrence]] = defaultdict(list)

    # 1. flow_rules
    for idx, line in enumerate(spec.flow_rules, start=1):
        normalized = normalize_phrase(line)
        if normalized:
            buckets[normalized].append(
                DuplicateOccurrence(
                    location=f"flow_rules[{idx}]",
                    original_text=line,
                )
            )

    # 2. faq_policy
    for idx, line in enumerate(spec.faq_policy, start=1):
        normalized = normalize_phrase(line)
        if normalized:
            buckets[normalized].append(
                DuplicateOccurrence(
                    location=f"faq_policy[{idx}]",
                    original_text=line,
                )
            )

    # 3. Policies — only sections declared by the channel profile.
    for section in channel_profile.policy_sections:
        for idx, line in enumerate(spec.policies.get_section(section.name), start=1):
            normalized = normalize_phrase(line)
            if normalized:
                buckets[normalized].append(
                    DuplicateOccurrence(
                        location=f"policies.{section.name}[{idx}]",
                        original_text=line,
                    )
                )

    duplicate_groups = [
        DuplicateRuleGroup(normalized_text=normalized, occurrences=occurrences)
        for normalized, occurrences in buckets.items()
        if len(occurrences) > 1
    ]

    # Stable sort: most-duplicated first, then alphabetical by normalized text.
    duplicate_groups.sort(key=lambda g: (-len(g.occurrences), g.normalized_text))

    return DeduplicationReport(duplicate_groups=duplicate_groups)