import ast
from pathlib import Path

ORCHESTRATION_DIR = Path("orchestration")
ALLOWED_JOB_TYPES = {"etl", "el", "tl", "rpt", "mon", "bk", "gov", "test"}


def test_each_dag_is_a_self_contained_convention_named_file() -> None:
    dag_files = sorted(ORCHESTRATION_DIR.glob("*.py"))

    assert dag_files
    assert not (ORCHESTRATION_DIR / "__init__.py").exists()
    assert not (ORCHESTRATION_DIR / "tasks.py").exists()

    for dag_file in dag_files:
        job_type, separator, description = dag_file.stem.partition("_")
        assert separator and description
        assert job_type in ALLOWED_JOB_TYPES

        module = ast.parse(dag_file.read_text(encoding="utf-8"))
        decorated_functions = {
            decorator.func.id
            for node in module.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            for decorator in node.decorator_list
            if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Name)
        }
        assert "flow" in decorated_functions
        assert "task" in decorated_functions


def test_prefect_entrypoints_reference_convention_named_dags() -> None:
    prefect_config = Path("prefect.yaml").read_text(encoding="utf-8")

    for dag_file in ORCHESTRATION_DIR.glob("*.py"):
        expected_entrypoint = f"entrypoint: {dag_file}:{dag_file.stem}"
        assert expected_entrypoint in prefect_config
