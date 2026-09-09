import ast
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
AGENT_ROOT = APP_ROOT / "agent"
CONTROLLED_FILES = [
    AGENT_ROOT / "nodes.py",
    *sorted((AGENT_ROOT / "routes").glob("*.py")),
]
MODEL_EXECUTION_NAMES = {
    "classify_route_with_llm",
    "parse_cost_question_with_llm",
    "generate_cost_analysis_with_llm",
}


def test_graph_nodes_do_not_bypass_runtime_control_plane() -> None:
    violations: list[str] = []
    for path in CONTROLLED_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in MODEL_EXECUTION_NAMES or _is_direct_tool(
                        alias.name
                    ):
                        violations.append(
                            f"{path.name}:{node.lineno}: import {alias.name}"
                        )
            if isinstance(node, ast.Call):
                name = _call_name(node.func)
                if name in MODEL_EXECUTION_NAMES or _is_direct_tool(name):
                    violations.append(f"{path.name}:{node.lineno}: call {name}")
    assert violations == []


def test_production_code_has_canonical_runtime_modules() -> None:
    runtime_modules = {
        path.relative_to(APP_ROOT).as_posix()
        for path in APP_ROOT.rglob("*.py")
        if "runtime" in path.stem or path.stem == "graph"
    }
    assert runtime_modules == {
        "agent/graph.py",
        "agent/runtime.py",
        "agent/runtime_contracts.py",
        "agent/runtime_services.py",
        "api/runtime_runs.py",
        "schemas/runtime.py",
        "services/runtime_service.py",
    }


def test_conversation_repositories_do_not_own_runtime_execution() -> None:
    forbidden_methods = {
        "claim_turn",
        "release_turn_reservation",
        "save_turn",
        "get_turn",
        "get_turn_by_message_id",
    }
    repository_paths = (
        APP_ROOT / "repositories" / "conversation_repository.py",
        APP_ROOT / "repositories" / "postgres_conversation_repository.py",
    )

    for path in repository_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        defined_methods = {
            node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
        }
        assert forbidden_methods.isdisjoint(defined_methods), path.name


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _is_direct_tool(name: str) -> bool:
    return name.endswith("_tool") and name not in {"invoke_tool"}
