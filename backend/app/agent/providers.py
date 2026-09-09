"""Minimal model provider adapter used by the graph nodes.

The adapter deliberately delegates to the existing DeepSeek service.  It is a
stable runtime seam for tests and future providers, not a second agent loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from jsonschema import Draft202012Validator
from jsonschema import ValidationError as JSONSchemaValidationError

from app.services import llm_service

ModelOperation = Literal[
    "classify_route", "parse_cost_question", "generate_cost_analysis"
]


@dataclass(frozen=True)
class ModelOperationSpec:
    operation: ModelOperation
    method_name: str
    output_schema: dict[str, Any]
    retry_policy: Literal["never", "transient"] = "transient"


MODEL_OPERATION_METHODS: dict[ModelOperation, str] = {
    "classify_route": "classify_route",
    "parse_cost_question": "parse_cost_question",
    "generate_cost_analysis": "generate_cost_analysis",
}
MODEL_OPERATION_SPECS: dict[ModelOperation, ModelOperationSpec] = {
    "classify_route": ModelOperationSpec(
        operation="classify_route",
        method_name="classify_route",
        output_schema={
            "type": "object",
            "required": ["route"],
            "properties": {"route": {"type": "string"}},
        },
    ),
    "parse_cost_question": ModelOperationSpec(
        operation="parse_cost_question",
        method_name="parse_cost_question",
        output_schema={
            "type": "object",
            "required": ["intent", "product_text", "period", "date_range"],
            "properties": {
                "intent": {"type": "string"},
                "product_text": {"type": "string"},
                "period": {"type": ["string", "null"]},
                "date_range": {"type": ["object", "null"]},
            },
        },
    ),
    "generate_cost_analysis": ModelOperationSpec(
        operation="generate_cost_analysis",
        method_name="generate_cost_analysis",
        output_schema={
            "type": "object",
            "required": ["analysis_text"],
            "properties": {"analysis_text": {"type": "string"}},
        },
    ),
}


class ModelContractError(ValueError):
    pass


def validate_model_operation_result(operation: str, value: Any) -> None:
    spec = MODEL_OPERATION_SPECS.get(operation)
    if spec is None:
        raise ModelContractError("模型操作未注册。")
    try:
        Draft202012Validator(spec.output_schema).validate(value)
    except JSONSchemaValidationError as exc:
        raise ModelContractError("模型结果不符合结构化契约。") from exc


class ModelProvider(Protocol):
    provider_id: str
    provider_version: str
    model_id: str

    def classify_route(self, *args: Any, **kwargs: Any) -> dict[str, Any]: ...

    def parse_cost_question(self, *args: Any, **kwargs: Any) -> dict[str, Any]: ...

    def generate_cost_analysis(self, *args: Any, **kwargs: Any) -> dict[str, Any]: ...

    def invoke_operation(
        self, operation: ModelOperation, *args: Any, **kwargs: Any
    ) -> dict[str, Any]: ...


class DeepSeekProviderAdapter:
    provider_id = "deepseek"
    provider_version = "deepseek-adapter-v2"

    @property
    def model_id(self) -> str:
        return llm_service.get_deepseek_model()

    def classify_route(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return llm_service.classify_route_with_llm(*args, **kwargs)

    def parse_cost_question(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return llm_service.parse_cost_question_with_llm(*args, **kwargs)

    def generate_cost_analysis(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return llm_service.generate_cost_analysis_with_llm(*args, **kwargs)

    def invoke_operation(
        self, operation: ModelOperation, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        method_name = MODEL_OPERATION_METHODS.get(operation)
        if method_name is None:
            raise ValueError(f"未知模型操作：{operation}")
        return getattr(self, method_name)(*args, **kwargs)


_DEFAULT_PROVIDER = DeepSeekProviderAdapter()


def get_model_provider() -> DeepSeekProviderAdapter:
    return _DEFAULT_PROVIDER
