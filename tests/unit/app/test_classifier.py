"""Unit tests for app/classifier.py — the fixed SSOT §10 System Prompt / Reference Asset split."""

from __future__ import annotations

from app.classifier import ContentClassifier, classify_spec
from app.schemas import FAQModel, ToolContract
from tests.conftest import build_minimal_agent_spec


def _faq(faq_id: str = "FAQ_PRIVACY") -> FAQModel:
    return FAQModel.model_validate(
        {
            "faq_id": faq_id,
            "type": "message",
            "match": ["what do you do with my data"],
            "say": ["We protect your data per our privacy policy."],
        }
    )


def _tool_contract(name: str = "book_appointment") -> ToolContract:
    return ToolContract.model_validate(
        {"name": name, "description": "Books an appointment.", "inputs": [], "outputs": []}
    )


def test_classify_routes_faqs_to_reference_asset_only():
    spec = build_minimal_agent_spec(faqs=[_faq()])
    classified = ContentClassifier().classify(spec)
    assert [f.faq_id for f in classified.reference_faqs] == ["FAQ_PRIVACY"]


def test_classify_routes_tool_contracts_to_reference_asset_only():
    spec = build_minimal_agent_spec(
        tools=["book_appointment"], tool_contracts=[_tool_contract()]
    )
    classified = ContentClassifier().classify(spec)
    assert [c.name for c in classified.reference_tool_contracts] == ["book_appointment"]


def test_classify_routes_tool_names_without_bodies_to_prompt():
    spec = build_minimal_agent_spec(
        tools=["book_appointment"], tool_contracts=[_tool_contract()]
    )
    classified = ContentClassifier().classify(spec)
    assert classified.prompt_tool_names == ["book_appointment"]


def test_classify_context_goes_to_reference_asset():
    spec = build_minimal_agent_spec()
    classified = ContentClassifier().classify(spec)
    assert classified.reference_context is spec.context


def test_classify_policies_are_exposed_for_the_prompt():
    spec = build_minimal_agent_spec()
    classified = ContentClassifier().classify(spec)
    assert classified.prompt_policies is spec.policies


def test_classify_faq_slice_is_a_new_list_not_the_same_object():
    spec = build_minimal_agent_spec(faqs=[_faq()])
    classified = ContentClassifier().classify(spec)
    assert classified.reference_faqs is not spec.faqs
    assert classified.reference_faqs == spec.faqs


def test_classify_spec_module_function_matches_class_based_classification():
    spec = build_minimal_agent_spec(faqs=[_faq()])
    via_function = classify_spec(spec)
    via_class = ContentClassifier().classify(spec)
    assert via_function.reference_faqs == via_class.reference_faqs
