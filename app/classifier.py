"""Deterministic split of ``AgentSpec`` into System Prompt vs Reference Asset.

Per SSOT §10 the classification is fixed and **not user-configurable**. If a
piece of content has the wrong destination, fix it here — never expose the
split as a runtime knob, otherwise two agents could end up with structurally
different prompts for the same source data.

Routing rules (SSOT §10.1):

- **System Prompt** — agent behavior and live state machine logic:
  identity, objectives, constants, input_variables, memory_slots,
  flow_rules, states, handlers, terminal_states, policies, tool **names**
  (without bodies) and faq_policy.
- **Reference Asset** — static information suitable for RAG indexing and
  out-of-band retrieval: faqs, tool_contracts (full bodies, including
  inputs/outputs/notes), and every subsection of ``context``
  (company_context, approved_services, summary_services_library,
  approved_process_*, support_and_trust).

The classifier itself is stateless and has no configuration. ``ClassifiedSpec``
is a frozen dataclass so the bundle cannot be reassigned downstream;
callers should treat the inner Pydantic objects as read-only.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas import (
    AgentSpec,
    ContextFile,
    FAQModel,
    PoliciesFile,
    ToolContract,
)


@dataclass(frozen=True, slots=True)
class ClassifiedSpec:
    """The output of the classifier — two views on the same ``AgentSpec``.

    The full :class:`AgentSpec` is preserved as ``spec`` so downstream
    stages can access fields not surfaced as slices (states, handlers,
    etc.). The ``reference_*`` slices feed the Reference Asset renderer;
    the ``prompt_*`` slices feed the System Prompt renderer.
    """

    spec: AgentSpec

    # → Reference Asset
    reference_faqs: list[FAQModel]
    reference_tool_contracts: list[ToolContract]
    reference_context: ContextFile

    # → System Prompt (slices; the rest is read directly from ``spec``)
    prompt_tool_names: list[str]
    prompt_policies: PoliciesFile


class ContentClassifier:
    """Apply the deterministic SSOT §10 split.

    Stateless. Instantiate once per process or once per call — both are
    cheap. There are no configuration knobs by design.
    """

    def classify(self, spec: AgentSpec) -> ClassifiedSpec:
        """Return a :class:`ClassifiedSpec` view of ``spec``.

        New ``list`` wrappers are produced for the FAQ and tool contract
        slices so callers can hold or mutate the slice without affecting
        the underlying ``AgentSpec``. The Pydantic objects themselves are
        shared (no deep copy) — the renderer treats them as read-only.
        """
        return ClassifiedSpec(
            spec=spec,
            reference_faqs=list(spec.faqs),
            reference_tool_contracts=list(spec.tool_contracts),
            reference_context=spec.context,
            prompt_tool_names=list(spec.tools),
            prompt_policies=spec.policies,
        )


def classify_spec(spec: AgentSpec) -> ClassifiedSpec:
    """Module-level convenience for ``ContentClassifier().classify(spec)``."""
    return ContentClassifier().classify(spec)