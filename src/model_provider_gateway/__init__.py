"""Fail-closed, LiteLLM-backed multi-provider gateway."""

from .capability_control import (
    build_owner_capability_plan,
    embedding_runtime_probe,
    owner_adapter_registry,
    validate_owner_capability_receipt,
)
from .catalog import catalog_list, catalog_show
from .consent import build_consent_plan, validate_consent
from .coverage import build_consumer_capability_matrix
from .execution import build_execution_request, execute_model_request, validate_execution_request
from .onboarding import OnboardingService, ProfileStore, onboarding_catalog
from .online_validation import prepare_online_validation_package, validate_online_validation_package
from .owner_execution import (
    build_capability_truth,
    build_owner_gate,
    build_owner_input_manifest,
    execute_owner_capability,
    owner_runtime_doctor,
    validate_owner_gate,
    validate_owner_input_manifest,
    write_owner_runtime_doctor,
)
from .profile import build_profile, validate_profile
from .route import build_route_plan, validate_route_plan
from .runtime import render_service_bundle

__all__ = [
    "build_consent_plan",
    "build_consumer_capability_matrix",
    "build_execution_request",
    "build_owner_capability_plan",
    "build_owner_gate",
    "build_owner_input_manifest",
    "build_capability_truth",
    "build_profile",
    "build_route_plan",
    "catalog_list",
    "catalog_show",
    "execute_model_request",
    "embedding_runtime_probe",
    "execute_owner_capability",
    "onboarding_catalog",
    "OnboardingService",
    "ProfileStore",
    "prepare_online_validation_package",
    "render_service_bundle",
    "owner_adapter_registry",
    "owner_runtime_doctor",
    "write_owner_runtime_doctor",
    "validate_consent",
    "validate_execution_request",
    "validate_online_validation_package",
    "validate_owner_capability_receipt",
    "validate_owner_gate",
    "validate_owner_input_manifest",
    "validate_profile",
    "validate_route_plan",
]

__version__ = "0.3.0"
