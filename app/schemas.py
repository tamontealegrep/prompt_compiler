"""Pydantic v2 schemas for the modular prompt compiler.

This module defines all the typed data models used by the loaders, classifier,
validator, deduplicator, renderer, and compiler. Schemas are split into:

- Compilation parameters (``CompilationParams`` and the supporting enums).
- Channel and compliance profiles (``ChannelProfile``, ``ComplianceProfile``).
- Manifest and includes (``ManifestConfig``, ``ManifestIncludes``,
  ``SubflowInstanceRef``).
- Static agent content (constants, input variables, tools, contracts, memory
  slots, identity, objectives, context, policies, flow rules, faq policy).
- Flow objects (``FlowObjectBase``, ``HandlerModel``, ``FAQModel``,
  ``StateModel``) — including the ``say_verbatim`` flag introduced in v2.
- Subflow templates (``SubflowTemplate``, ``TemplateParamDefinition``,
  ``SubflowExports``).
- Aggregate ``AgentSpec`` plus the pipeline output dataclasses
  (``CompilationStats``, ``CompilationOutputs``).

All schemas inherit from ``StrictModel`` which forbids unknown fields and trims
whitespace from string inputs. ``PoliciesFragmentFile`` is the only exception:
its section names are dynamic (driven by the active channel profile), so it
uses ``extra='allow'`` with a ``model_validator`` that vets every dynamic
section.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

if TYPE_CHECKING:
    # Forward references resolved by the type checker only. The runtime classes
    # live in ``app/validators.py`` and ``app/deduplicator.py`` and are not
    # imported here in order to avoid an import cycle.
    from app.deduplicator import DeduplicationReport
    from app.validators import ValidationReport


# ---------------------------------------------------------------------------
# Type aliases and regex patterns
# ---------------------------------------------------------------------------

YES_NO = Literal["yes", "no"]
NODE_TYPE = Literal["message", "question", "decision", "action", "registration", "terminal", "start", "subflow_change"]

LOWER_SNAKE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
UPPER_CONST_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
UPPER_ID_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
RULE_ID_RE = re.compile(r"^[A-Z]+_[0-9]+$")


# ---------------------------------------------------------------------------
# Strict base model and validation helpers
# ---------------------------------------------------------------------------


class StrictModel(BaseModel):
    """Base model: forbid extra fields, trim string inputs, validate on assignment."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=True,
    )


def validate_pattern(value: str, pattern: re.Pattern[str], label: str) -> str:
    """Return ``value`` if it matches ``pattern``; otherwise raise ``ValueError``."""
    if not value or not pattern.fullmatch(value):
        raise ValueError(f"{label} inválido: {value!r}")
    return value


def validate_non_empty_lines(
    lines: list[str], label: str, *, allow_empty: bool = True
) -> list[str]:
    """Validate that every item in ``lines`` is a non-empty trimmed string.

    If ``allow_empty`` is ``False``, the list itself must be non-empty too.
    """
    if not allow_empty and not lines:
        raise ValueError(f"{label} no puede estar vacío.")
    for item in lines:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{label} contiene un string vacío o inválido.")
    return lines


# ---------------------------------------------------------------------------
# Compilation parameters and enums
# ---------------------------------------------------------------------------


class ChannelType(str, Enum):
    """Delivery channel of the compiled agent."""

    VOICE = "voice"
    CHAT = "chat"
    ASYNC_TEXT = "async_text"


class VerbosityLevel(str, Enum):
    """Controls how much auxiliary context is included in the system prompt."""

    MINIMAL = "minimal"
    STANDARD = "standard"
    VERBOSE = "verbose"


class ReferenceAssetFormat(str, Enum):
    """Output format for the auxiliary Reference Asset."""

    MARKDOWN = "markdown"
    JSON = "json"


class ContentTarget(str, Enum):
    """Where a piece of agent content is rendered."""

    SYSTEM_PROMPT = "system_prompt"
    REFERENCE_ASSET = "reference_asset"


class ComplianceSeverity(str, Enum):
    """Severity assigned to a compliance rule violation."""

    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class CompilationParams:
    """User-facing knobs that drive the compile pipeline.

    Constructed by the CLI from argparse, then threaded through ``compiler.py``,
    ``loaders.py`` and ``renderers.py``. Frozen and slotted so it cannot be
    mutated accidentally during compilation.
    """

    channel: ChannelType = ChannelType.VOICE
    max_prompt_tokens: int | None = None
    verbosity: VerbosityLevel = VerbosityLevel.STANDARD
    include_reference_asset: bool = True
    reference_asset_formats: list[ReferenceAssetFormat] = field(
        default_factory=lambda: [
            ReferenceAssetFormat.MARKDOWN,
            ReferenceAssetFormat.JSON,
        ]
    )
    compliance_profile: str | None = None
    embed_subflows: bool = True
    """When True, all subflow states are rendered inline in the system prompt
    and no separate subflow reference documents are produced.  Use this when
    the deployment platform expects a single self-contained file."""


# ---------------------------------------------------------------------------
# Channel profile
# ---------------------------------------------------------------------------


class PolicySectionDefinition(StrictModel):
    """One row in a channel profile's ``policy_sections`` list."""

    name: str
    label: str
    required: bool = False

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return validate_pattern(v, LOWER_SNAKE_RE, "policy_section.name")

    @field_validator("label")
    @classmethod
    def validate_label(cls, v: str) -> str:
        return validate_pattern(v, UPPER_CONST_RE, "policy_section.label")


class ChannelProfile(StrictModel):
    """Channel-specific compilation profile loaded from ``profiles/channels/*.yaml``."""

    channel: ChannelType
    display_name: str
    policy_sections: list[PolicySectionDefinition]

    @field_validator("policy_sections")
    @classmethod
    def validate_unique_section_names(
        cls, v: list[PolicySectionDefinition]
    ) -> list[PolicySectionDefinition]:
        names = [s.name for s in v]
        if len(names) != len(set(names)):
            raise ValueError(
                "channel_profile.policy_sections contiene nombres duplicados."
            )
        return v


# ---------------------------------------------------------------------------
# Compliance profile
# ---------------------------------------------------------------------------


class ComplianceRuleDefinition(StrictModel):
    """One rule entry inside a compliance profile."""

    rule_id: str
    description: str
    severity: ComplianceSeverity
    check: str  # identifier of the registered checker function

    @field_validator("rule_id")
    @classmethod
    def validate_rule_id(cls, v: str) -> str:
        return validate_pattern(v, RULE_ID_RE, "rule_id")

    @field_validator("check")
    @classmethod
    def validate_check(cls, v: str) -> str:
        return validate_pattern(v, LOWER_SNAKE_RE, "compliance_rule.check")


class ComplianceProfile(StrictModel):
    """Compliance profile loaded from ``profiles/compliance/*.yaml``."""

    profile_id: str
    display_name: str
    rules: list[ComplianceRuleDefinition]

    @field_validator("profile_id")
    @classmethod
    def validate_profile_id(cls, v: str) -> str:
        return validate_pattern(v, LOWER_SNAKE_RE, "profile_id")

    @field_validator("rules")
    @classmethod
    def validate_unique_rule_ids(
        cls, v: list[ComplianceRuleDefinition]
    ) -> list[ComplianceRuleDefinition]:
        ids = [r.rule_id for r in v]
        if len(ids) != len(set(ids)):
            raise ValueError("compliance_profile.rules contiene rule_id duplicados.")
        return v


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


class ManifestIncludes(StrictModel):
    """Lists of relative paths referenced from the agent's ``manifest.yaml``."""

    memory_slots: list[str] = Field(default_factory=list)
    flow_rules: list[str] = Field(default_factory=list)
    faq_policy: list[str] = Field(default_factory=list)
    policies: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    tool_contracts: list[str] = Field(default_factory=list)
    handlers: list[str] = Field(default_factory=list)
    faqs: list[str] = Field(default_factory=list)
    states: list[str] = Field(default_factory=list)
    terminal_states: list[str] = Field(default_factory=list)

    @field_validator(
        "memory_slots",
        "flow_rules",
        "faq_policy",
        "policies",
        "tools",
        "tool_contracts",
        "handlers",
        "faqs",
        "states",
        "terminal_states",
    )
    @classmethod
    def validate_paths(cls, v: list[str]) -> list[str]:
        for item in v:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("Cada include path debe ser un string no vacío.")
        return v


class SubflowInstanceRef(StrictModel):
    """Manifest entry that instantiates a subflow template with optional params."""

    template: str
    instance_id: str
    namespace: Optional[str] = None
    params: dict[str, str] = Field(default_factory=dict)

    @field_validator("instance_id")
    @classmethod
    def validate_instance_id(cls, v: str) -> str:
        return validate_pattern(v, LOWER_SNAKE_RE, "subflow_instance.instance_id")

    @field_validator("namespace")
    @classmethod
    def validate_namespace(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return validate_pattern(v, LOWER_SNAKE_RE, "subflow_instance.namespace")

    @field_validator("template")
    @classmethod
    def validate_template(cls, v: str) -> str:
        if not v:
            raise ValueError("subflow_instance.template no puede estar vacío.")
        return v

    @model_validator(mode="after")
    def set_default_namespace(self) -> "SubflowInstanceRef":
        if self.namespace is None:
            # Default the namespace to the instance_id so consumers can rely on
            # a non-null value when generating namespaced state and slot names.
            self.namespace = self.instance_id
        return self


class ManifestConfig(StrictModel):
    """Top-level config loaded from each agent's ``manifest.yaml``.

    The legacy ``channel`` and ``output_path`` fields are removed in v2:
    channel is now a ``CompilationParam`` (CLI flag) and the output directory
    is the convention ``dist/{agent_id}/``.
    """

    agent_id: str
    version: str = "1.0.0"
    language: str
    start_at: str
    template_path: str = "templates/system_prompt.md.j2"
    dynamic_state_slots: list[str] = Field(
        default_factory=lambda: ["current_state", "resume_state"]
    )
    includes: ManifestIncludes = Field(default_factory=ManifestIncludes)
    subflow_instances: list[SubflowInstanceRef] = Field(default_factory=list)

    @field_validator("agent_id")
    @classmethod
    def validate_agent_id(cls, v: str) -> str:
        return validate_pattern(v, LOWER_SNAKE_RE, "agent_id")

    @field_validator("start_at")
    @classmethod
    def validate_start_at(cls, v: str) -> str:
        return validate_pattern(v, UPPER_ID_RE, "manifest.start_at")

    @field_validator("dynamic_state_slots")
    @classmethod
    def validate_dynamic_state_slots(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("dynamic_state_slots no puede estar vacío.")
        for item in v:
            validate_pattern(item, LOWER_SNAKE_RE, "dynamic_state_slot")
        return v

    @field_validator("subflow_instances")
    @classmethod
    def validate_subflow_instances(
        cls, v: list[SubflowInstanceRef]
    ) -> list[SubflowInstanceRef]:
        seen: set[str] = set()
        for item in v:
            if item.instance_id in seen:
                raise ValueError(
                    f"instance_id duplicado en manifest.subflow_instances: {item.instance_id!r}"
                )
            seen.add(item.instance_id)
        return v


# ---------------------------------------------------------------------------
# Static agent content
# ---------------------------------------------------------------------------


class ConstantItem(StrictModel):
    """One row in ``constants.yaml``."""

    name: str
    description: str
    value: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return validate_pattern(v, UPPER_CONST_RE, "constant.name")


class ConstantsFile(StrictModel):
    """Schema for ``constants.yaml``."""

    constants: list[ConstantItem]

    @field_validator("constants")
    @classmethod
    def validate_constants(cls, v: list[ConstantItem]) -> list[ConstantItem]:
        if not v:
            raise ValueError("constants no puede estar vacío.")
        return v


class InputVariable(StrictModel):
    """Runtime variable injected by the platform at execution time."""

    name: str
    description: str
    allowed_values: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return validate_pattern(v, LOWER_SNAKE_RE, "input_variable.name")


class InputVariablesFile(StrictModel):
    """Schema for ``input_variables.yaml``."""

    input_variables: list[InputVariable]

    @field_validator("input_variables")
    @classmethod
    def validate_input_variables(cls, v: list[InputVariable]) -> list[InputVariable]:
        if not v:
            raise ValueError("input_variables no puede estar vacío.")
        return v


class ToolsFragmentFile(StrictModel):
    """Schema for fragments of tool declarations (e.g. ``shared/tools/*.yaml``)."""

    tools: list[str] = Field(default_factory=list)

    @field_validator("tools")
    @classmethod
    def validate_tools(cls, v: list[str]) -> list[str]:
        for item in v:
            validate_pattern(item, LOWER_SNAKE_RE, "tool")
        return v


class ToolsFile(ToolsFragmentFile):
    """Schema for the merged list of tools — must be non-empty."""

    @model_validator(mode="after")
    def validate_non_empty_tools(self) -> "ToolsFile":
        if not self.tools:
            raise ValueError("tools no puede estar vacío.")
        return self


class ToolContractField(StrictModel):
    """Input or output field declared in a tool contract."""

    name: str
    required: bool = True
    description: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return validate_pattern(v, LOWER_SNAKE_RE, "tool_contract_field.name")


class ToolContract(StrictModel):
    """A complete contract for one tool — used by Reference Asset and validators."""

    name: str
    description: str
    inputs: list[ToolContractField] = Field(default_factory=list)
    outputs: list[ToolContractField] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return validate_pattern(v, LOWER_SNAKE_RE, "tool_contract.name")

    @field_validator("notes")
    @classmethod
    def validate_notes(cls, v: list[str]) -> list[str]:
        return validate_non_empty_lines(v, "tool_contract.notes", allow_empty=True)


class ToolContractsFragmentFile(StrictModel):
    """Schema for fragments of tool contract declarations."""

    tool_contracts: list[ToolContract] = Field(default_factory=list)


class ToolContractsFile(ToolContractsFragmentFile):
    """Schema for the merged list of tool contracts — must be non-empty."""

    @model_validator(mode="after")
    def validate_contracts(self) -> "ToolContractsFile":
        if not self.tool_contracts:
            raise ValueError("tool_contracts no puede estar vacío.")
        return self


class MemorySlot(StrictModel):
    """A typed memory slot used during the conversation."""

    name: str
    description: str
    kind: Literal[
        "dynamic_state",
        "captured",
        "derived",
        "control",
        "user_data",
        "tool_output",
        "retry_counter",
        "other",
    ] = "other"

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return validate_pattern(v, LOWER_SNAKE_RE, "memory_slot.name")


class MemorySlotsFile(StrictModel):
    """Schema for ``memory_slots.yaml``."""

    memory_slots: list[MemorySlot] = Field(default_factory=list)


class IdentityFile(StrictModel):
    """Schema for ``identity.yaml``."""

    identity: list[str]

    @field_validator("identity")
    @classmethod
    def validate_identity(cls, v: list[str]) -> list[str]:
        return validate_non_empty_lines(v, "identity", allow_empty=False)


class ObjectivesFile(StrictModel):
    """Schema for ``objectives.yaml``."""

    primary_objective: list[str]
    secondary_objectives: list[str]
    success_alternatives: list[str]

    @field_validator("primary_objective", "secondary_objectives", "success_alternatives")
    @classmethod
    def validate_sections(cls, v: list[str]) -> list[str]:
        return validate_non_empty_lines(v, "objectives section", allow_empty=False)


class SummaryServiceItem(StrictModel):
    """One entry in ``context.summary_services_library``."""

    key: str
    procedure: str
    text: str

    @field_validator("key")
    @classmethod
    def validate_key(cls, v: str) -> str:
        return validate_pattern(v, UPPER_ID_RE, "summary_service.key")

    @field_validator("procedure")
    @classmethod
    def validate_procedure(cls, v: str) -> str:
        return validate_pattern(v, LOWER_SNAKE_RE, "summary_service.procedure")


class ApprovedProcessStep(StrictModel):
    """One step in ``context.approved_process_steps``."""

    title: str
    text: str


class ContextFile(StrictModel):
    """Schema for ``context.yaml`` — purely static information about company/services."""

    company_context: list[str]
    approved_services: list[str]
    summary_services_library: list[SummaryServiceItem]
    approved_process_intro: str
    approved_process_steps: list[ApprovedProcessStep]
    support_and_trust: list[str]

    @field_validator("company_context", "approved_services", "support_and_trust")
    @classmethod
    def validate_context_lists(cls, v: list[str]) -> list[str]:
        return validate_non_empty_lines(v, "context section", allow_empty=False)

    @field_validator("summary_services_library")
    @classmethod
    def validate_summaries(
        cls, v: list[SummaryServiceItem]
    ) -> list[SummaryServiceItem]:
        if not v:
            raise ValueError("summary_services_library no puede estar vacío.")
        return v

    @field_validator("approved_process_steps")
    @classmethod
    def validate_process_steps(
        cls, v: list[ApprovedProcessStep]
    ) -> list[ApprovedProcessStep]:
        if not v:
            raise ValueError("approved_process_steps no puede estar vacío.")
        return v


# ---------------------------------------------------------------------------
# Policies (dynamic — sections come from the channel profile)
# ---------------------------------------------------------------------------


class PoliciesFragmentFile(StrictModel):
    """Schema for a policies YAML fragment.

    Section names are dynamic — the loader validates them at merge time against
    the active channel profile. Each section value must be a list of non-empty
    strings.
    """

    model_config = ConfigDict(
        extra="allow",
        validate_assignment=True,
        str_strip_whitespace=True,
    )

    @model_validator(mode="before")
    @classmethod
    def accept_dynamic_sections(cls, data: Any) -> dict[str, Any]:
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise ValueError("Un archivo de policies debe ser un mapping YAML.")
        for key, value in data.items():
            if not isinstance(value, list):
                raise ValueError(
                    f"La sección de políticas {key!r} debe ser una lista."
                )
            for item in value:
                if not isinstance(item, str) or not item.strip():
                    raise ValueError(
                        f"La sección {key!r} contiene un string vacío o inválido."
                    )
        return data

    def get_section(self, name: str) -> list[str]:
        """Return the lines of ``name`` section, or an empty list if absent."""
        value = getattr(self, name, None)
        return list(value) if value else []

    def all_sections(self) -> dict[str, list[str]]:
        """Return all sections as a dict, including dynamically declared ones."""
        return self.model_dump()


class PoliciesFile(PoliciesFragmentFile):
    """Final merged policies after channel-profile validation.

    Same shape as ``PoliciesFragmentFile``; subclassed for semantic clarity so
    consumers can type-hint against ``PoliciesFile`` to signal they expect the
    merged result rather than a raw fragment.
    """


# ---------------------------------------------------------------------------
# Flow rules and FAQ policy
# ---------------------------------------------------------------------------


class FlowRulesFile(StrictModel):
    """Schema for a ``flow_rules.yaml`` fragment."""

    flow_rules: list[str]

    @field_validator("flow_rules")
    @classmethod
    def validate_flow_rules(cls, v: list[str]) -> list[str]:
        return validate_non_empty_lines(v, "flow_rules", allow_empty=False)


class FAQPolicyFile(StrictModel):
    """Schema for a ``faq_policy.yaml`` fragment."""

    faq_policy: list[str]

    @field_validator("faq_policy")
    @classmethod
    def validate_faq_policy(cls, v: list[str]) -> list[str]:
        return validate_non_empty_lines(v, "faq_policy", allow_empty=False)


# ---------------------------------------------------------------------------
# Flow objects (handlers, FAQs, states)
# ---------------------------------------------------------------------------


class CaptureField(StrictModel):
    """One slot to capture from a flow object."""

    slot: str
    type_expr: str

    @field_validator("slot")
    @classmethod
    def validate_slot(cls, v: str) -> str:
        return validate_pattern(v, LOWER_SNAKE_RE, "capture.slot")


class FlowObjectBase(StrictModel):
    """Common fields and semantics shared by handlers and states.

    The ``say_verbatim`` flag (introduced in v2) controls how the renderer
    annotates SAY blocks: ``False`` → ``[flexible]`` (paraphrasable),
    ``True`` → ``[verbatim]`` (literal). The flag has no semantic effect at
    schema level — it is consumed by ``app/renderers.py``.
    """

    type: NODE_TYPE
    goal: list[str] = Field(default_factory=list)
    do: list[str] = Field(default_factory=list)
    say: list[str] = Field(default_factory=list)
    say_verbatim: bool = False
    wait: Optional[YES_NO] = None
    capture: list[CaptureField] = Field(default_factory=list)
    store: list[str] = Field(default_factory=list)
    route: list[str] = Field(default_factory=list)
    faq_resume_to: Optional[str] = None
    fallback: list[str] = Field(default_factory=list)
    execute: Optional[str] = None
    final: Optional[YES_NO] = None

    @field_validator("goal", "do", "say", "store", "route", "fallback")
    @classmethod
    def validate_line_lists(cls, v: list[str]) -> list[str]:
        return validate_non_empty_lines(v, "flow object section", allow_empty=True)

    @field_validator("faq_resume_to")
    @classmethod
    def validate_resume_target(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        # ``@instance.export`` aliases are allowed here; resolved before graph validation.
        if v.startswith("@"):
            return v
        return validate_pattern(v, UPPER_ID_RE, "faq_resume_to")

    @field_validator("execute")
    @classmethod
    def validate_execute(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return validate_pattern(v, LOWER_SNAKE_RE, "execute")

    @model_validator(mode="after")
    def validate_semantics(self) -> "FlowObjectBase":
        if self.type == "message":
            if self.wait != "no":
                raise ValueError("Los objetos de type=message deben tener wait='no'.")
            if not self.say:
                raise ValueError("Los objetos de type=message deben tener SAY.")
            if self.execute is not None:
                raise ValueError("Los objetos de type=message no deben tener EXECUTE.")
        elif self.type == "question":
            if self.wait != "yes":
                raise ValueError("Los objetos de type=question deben tener wait='yes'.")
            if not self.say:
                raise ValueError("Los objetos de type=question deben tener SAY.")
        elif self.type == "decision":
            if self.wait not in (None, "no"):
                raise ValueError("Los objetos de type=decision no deben usar wait='yes'.")
            if self.say:
                raise ValueError("Los objetos de type=decision no deben tener SAY.")
        elif self.type == "action":
            if self.wait != "no":
                raise ValueError("Los objetos de type=action deben tener wait='no'.")
            if not self.execute:
                raise ValueError("Los objetos de type=action deben declarar EXECUTE.")
        elif self.type == "registration":
            if self.wait != "no":
                raise ValueError("Los objetos de type=registration deben tener wait='no'.")
            if self.execute is not None:
                raise ValueError("Los objetos de type=registration no deben tener EXECUTE.")
        elif self.type == "terminal":
            if self.wait != "no":
                raise ValueError("Los objetos de type=terminal deben tener wait='no'.")
            if self.final != "yes":
                raise ValueError("Los objetos de type=terminal deben tener final='yes'.")
        elif self.type == "start":
            if self.wait != "no":
                raise ValueError("Los objetos de type=start deben tener wait='no'.")
            if self.final == "yes":
                raise ValueError("Los objetos de type=start no deben tener final='yes'.")
        elif self.type == "subflow_change":
            if self.wait != "no":
                raise ValueError("Los objetos de type=subflow_change deben tener wait='no'.")
            if self.final == "yes":
                raise ValueError("Los objetos de type=subflow_change no deben tener final='yes'.")

        if self.type not in ("terminal",) and not self.route and not self.fallback:
            raise ValueError(
                "Los objetos no terminales deben tener ROUTE o FALLBACK."
            )

        return self


class HandlerModel(FlowObjectBase):
    """A global interrupt available from any active state."""

    handler_id: str
    trigger: list[str] = Field(default_factory=list)

    @field_validator("handler_id")
    @classmethod
    def validate_handler_id(cls, v: str) -> str:
        return validate_pattern(v, UPPER_ID_RE, "handler_id")

    @field_validator("trigger")
    @classmethod
    def validate_trigger(cls, v: list[str]) -> list[str]:
        return validate_non_empty_lines(v, "handler.trigger", allow_empty=False)


class HandlersFile(StrictModel):
    """Schema for handler files (local or shared)."""

    handlers: list[HandlerModel]

    @field_validator("handlers")
    @classmethod
    def validate_handlers(cls, v: list[HandlerModel]) -> list[HandlerModel]:
        if not v:
            raise ValueError("handlers no puede estar vacío.")
        return v


class FAQModel(StrictModel):
    """An approved FAQ card — semantic match phrases plus an answer payload."""

    faq_id: str
    type: Literal["message"]
    match: list[str]
    answer: list[str]

    @field_validator("faq_id")
    @classmethod
    def validate_faq_id(cls, v: str) -> str:
        return validate_pattern(v, UPPER_ID_RE, "faq_id")

    @field_validator("match")
    @classmethod
    def validate_match(cls, v: list[str]) -> list[str]:
        return validate_non_empty_lines(v, "faq.match", allow_empty=False)

    @field_validator("answer")
    @classmethod
    def validate_answer(cls, v: list[str]) -> list[str]:
        return validate_non_empty_lines(v, "faq.answer", allow_empty=False)


class FAQsFile(StrictModel):
    """Schema for FAQ catalog files."""

    faqs: list[FAQModel]

    @field_validator("faqs")
    @classmethod
    def validate_faqs(cls, v: list[FAQModel]) -> list[FAQModel]:
        if not v:
            raise ValueError("faqs no puede estar vacío.")
        return v


class StateModel(FlowObjectBase):
    """A node in the main conversational state machine."""

    state_id: str

    @field_validator("state_id")
    @classmethod
    def validate_state_id(cls, v: str) -> str:
        return validate_pattern(v, UPPER_ID_RE, "state_id")


class StatesFile(StrictModel):
    """Schema for non-terminal state files."""

    states: list[StateModel]

    @field_validator("states")
    @classmethod
    def validate_states(cls, v: list[StateModel]) -> list[StateModel]:
        if not v:
            raise ValueError("states no puede estar vacío.")
        return v


class TerminalStatesFile(StrictModel):
    """Schema for terminal state files. Every entry must have type=terminal and final=yes."""

    terminal_states: list[StateModel]

    @field_validator("terminal_states")
    @classmethod
    def validate_terminal_states(cls, v: list[StateModel]) -> list[StateModel]:
        if not v:
            raise ValueError("terminal_states no puede estar vacío.")
        for state in v:
            if state.type != "terminal":
                raise ValueError(
                    f"El archivo terminal_states solo puede contener type=terminal. "
                    f"Se encontró {state.state_id} con type={state.type!r}."
                )
            if state.final != "yes":
                raise ValueError(
                    f"El estado terminal {state.state_id} debe tener final='yes'."
                )
        return v


# ---------------------------------------------------------------------------
# Subflow templates
# ---------------------------------------------------------------------------


class TemplateParamDefinition(StrictModel):
    """Definition of a parameter consumed by a subflow template."""

    name: str
    description: str = ""
    required: bool = True
    default: Optional[str] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return validate_pattern(v, LOWER_SNAKE_RE, "template_param.name")


class SubflowExports(StrictModel):
    """Maps export aliases used by other subflows to local state ids and slot names."""

    states: dict[str, str] = Field(default_factory=dict)
    slots: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_exports(self) -> "SubflowExports":
        for key, value in self.states.items():
            validate_pattern(key, LOWER_SNAKE_RE, "subflow_exports.states key")
            validate_pattern(value, UPPER_ID_RE, "subflow_exports.states value")
        for key, value in self.slots.items():
            validate_pattern(key, LOWER_SNAKE_RE, "subflow_exports.slots key")
            validate_pattern(value, LOWER_SNAKE_RE, "subflow_exports.slots value")
        return self


class SubflowTemplate(StrictModel):
    """A reusable subflow template, instantiated from the manifest with parameters."""

    template_id: str
    description: str
    params: list[TemplateParamDefinition] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)

    # Slots declared here are namespaced per instance during instantiation.
    local_memory_slots: list[MemorySlot] = Field(default_factory=list)

    flow_rules: list[str] = Field(default_factory=list)
    faq_policy: list[str] = Field(default_factory=list)
    handlers: list[HandlerModel] = Field(default_factory=list)
    faqs: list[FAQModel] = Field(default_factory=list)
    states: list[StateModel] = Field(default_factory=list)
    terminal_states: list[StateModel] = Field(default_factory=list)
    exports: SubflowExports = Field(default_factory=SubflowExports)

    @field_validator("template_id")
    @classmethod
    def validate_template_id(cls, v: str) -> str:
        return validate_pattern(v, LOWER_SNAKE_RE, "subflow_template.template_id")

    @field_validator("required_tools")
    @classmethod
    def validate_required_tools(cls, v: list[str]) -> list[str]:
        for item in v:
            validate_pattern(item, LOWER_SNAKE_RE, "subflow_template.required_tool")
        return v

    @field_validator("flow_rules", "faq_policy")
    @classmethod
    def validate_text_lists(cls, v: list[str]) -> list[str]:
        return validate_non_empty_lines(v, "subflow_template text list", allow_empty=True)

    @model_validator(mode="after")
    def validate_subflow_template(self) -> "SubflowTemplate":
        param_names: set[str] = set()
        for item in self.params:
            if item.name in param_names:
                raise ValueError(
                    f"Parámetro duplicado en template {self.template_id!r}: {item.name!r}"
                )
            param_names.add(item.name)

        has_content = any(
            [
                self.local_memory_slots,
                self.flow_rules,
                self.faq_policy,
                self.handlers,
                self.faqs,
                self.states,
                self.terminal_states,
            ]
        )
        if not has_content:
            raise ValueError(
                f"El subflow template {self.template_id!r} no aporta ningún contenido."
            )

        local_state_ids = {s.state_id for s in self.states} | {
            s.state_id for s in self.terminal_states
        }
        for export_name, state_id in self.exports.states.items():
            if state_id not in local_state_ids:
                raise ValueError(
                    f"El export state {export_name!r} del template {self.template_id!r} "
                    f"apunta a {state_id!r}, pero ese state no existe dentro del template."
                )

        for export_name, slot_name in self.exports.slots.items():
            if not slot_name:
                raise ValueError(
                    f"El export slot {export_name!r} del template "
                    f"{self.template_id!r} es inválido."
                )

        for state in self.terminal_states:
            if state.type != "terminal":
                raise ValueError(
                    f"El subflow template {self.template_id!r} tiene {state.state_id!r} "
                    f"en terminal_states pero su type no es terminal."
                )
            if state.final != "yes":
                raise ValueError(
                    f"El terminal state {state.state_id!r} del template "
                    f"{self.template_id!r} debe tener final='yes'."
                )

        return self


# ---------------------------------------------------------------------------
# Aggregate spec and pipeline outputs
# ---------------------------------------------------------------------------


class AgentSpec(StrictModel):
    """The fully merged agent specification fed into the rest of the pipeline.

    Built by ``app/loaders.load_agent_spec`` from the manifest, shared includes
    and instantiated subflows. Carries provenance metadata
    (``object_sources``) and subflow export maps so downstream stages can
    surface helpful diagnostics.
    """

    manifest: ManifestConfig

    constants: list[ConstantItem]
    input_variables: list[InputVariable]
    tools: list[str]
    tool_contracts: list[ToolContract]
    memory_slots: list[MemorySlot]
    identity: list[str]
    objectives: ObjectivesFile
    context: ContextFile
    policies: PoliciesFile

    flow_rules: list[str]
    faq_policy: list[str]
    handlers: list[HandlerModel]
    faqs: list[FAQModel]
    states: list[StateModel]
    terminal_states: list[StateModel]

    object_sources: dict[str, list[str]] = Field(default_factory=dict)
    instance_state_exports: dict[str, dict[str, str]] = Field(default_factory=dict)
    instance_slot_exports: dict[str, dict[str, str]] = Field(default_factory=dict)

    @property
    def all_state_ids(self) -> list[str]:
        return [s.state_id for s in self.states] + [
            s.state_id for s in self.terminal_states
        ]

    @property
    def main_state_ids(self) -> list[str]:
        return [s.state_id for s in self.states]


@dataclass(frozen=True, slots=True)
class CompilationStats:
    """Lightweight numerical summary of a compilation run."""

    total_states: int
    total_handlers: int
    total_faqs: int
    total_subflows_instantiated: int
    duplicate_rules_found: int
    estimated_system_prompt_chars: int
    estimated_reference_asset_chars: int
    estimated_subflows_chars: int


@dataclass(frozen=True, slots=True)
class CompilationOutputs:
    """Bundle of every artifact produced by ``compile_agent``.

    The ``validation_report`` and ``deduplication_report`` annotations are
    forward references resolved by the type checker via ``TYPE_CHECKING``.
    The runtime classes live in ``app/validators.py`` and
    ``app/deduplicator.py``; they are not imported here to avoid a cycle.
    """

    agent_id: str
    system_prompt: str
    subflow_documents: dict[str, str]
    reference_asset_markdown: str | None
    reference_asset_json: dict[str, Any] | None
    validation_report: ValidationReport
    deduplication_report: DeduplicationReport
    orphan_report: str
    stats: CompilationStats
