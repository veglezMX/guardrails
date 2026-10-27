"""Executable import rules from folder-structure.md, without service assembly.

Inspect every import statement, including function-local imports, and exercise
the real public modules in fresh Python processes whose server imports are
blocked. Native LiteLLM startup remains covered by the separate gateway case.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

# Inspect the package selected by the test environment, also when this new test
# is verified against the integration checkout before it is copied there.
MVP = Path(importlib.util.find_spec("guardrails").origin).resolve().parent.parent
DOMAIN = (
    "errors",
    "models",
    "ports",
    "compiler",
    "config",
    "transformations",
)
PUBLIC = ("errors", "models", "transformations", "egress")
INFRASTRUCTURE = (
    "fastapi",
    "starlette",
    "sqlalchemy",
    "psycopg",
    "psycopg2",
    "sqlite3",
    "litellm",
    "httpx",
    "httpcore",
    "requests",
    "urllib3",
    "aiohttp",
)
NETWORK_STDLIB = ("socket", "ssl", "http", "urllib", "ftplib", "smtplib")
ASSEMBLY_ONLY = ("tests", "pytest", "proof", "evolution", "deploy", "plans")


def imports(path):
    """Return absolute targets of static and literal dynamic imports."""
    relative = path.relative_to(MVP).with_suffix("")
    module = ".".join(relative.parts)
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    result = []
    for node in ast.walk(ast.parse(path.read_text(), filename=str(relative))):
        if isinstance(node, ast.Import):
            result.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = importlib.util.resolve_name("." * node.level + base, package)
            result.extend(base + "." + alias.name for alias in node.names)
        elif (
            isinstance(node, ast.Call)
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and (
                isinstance(node.func, ast.Name)
                and node.func.id in {"__import__", "import_module"}
                or isinstance(node.func, ast.Attribute)
                and node.func.attr == "import_module"
            )
        ):
            value = node.args[0].value
            result.append(
                importlib.util.resolve_name(value, package)
                if value.startswith(".")
                else value
            )
    return result


def under(name, prefixes):
    return any(name == prefix or name.startswith(prefix + ".") for prefix in prefixes)


def isolated_imports(modules, allowed_domain, blocked, exercise):
    """No output contains environment variables, content, credentials or paths."""
    script = """
import importlib, importlib.abc, json, sys
sys.path.insert(0, sys.argv[1])
spec = json.loads(sys.argv[2])
def matches(name, prefixes):
    return any(name == p or name.startswith(p + '.') for p in prefixes)
class Boundary(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if matches(fullname, spec['blocked']):
            raise ImportError('forbidden_dependency')
        if fullname.startswith('guardrails.evolution.') and not matches(fullname, spec['allowed']):
            raise ImportError('private_service_dependency')
        return None
sys.meta_path.insert(0, Boundary())
# A positive control proves that installed database/web packages cannot bypass
# the blocker. The runtime import result must then succeed under that blocker.
for name in spec['controls']:
    try:
        importlib.import_module(name)
    except ImportError as exc:
        if str(exc) not in {'forbidden_dependency', 'private_service_dependency'}:
            raise SystemExit('boundary_control_failed')
    else:
        raise SystemExit('boundary_control_failed')
for name in spec['modules']:
    importlib.import_module(name)
exec(spec['exercise'], {})
unexpected = [name for name in sys.modules if matches(name, spec['blocked'])
    or name.startswith('guardrails.evolution.') and not matches(name, spec['allowed'])]
if unexpected:
    raise SystemExit('forbidden_module_loaded')
print(json.dumps({'imported': spec['modules'], 'forbidden_loaded': False}))
"""
    spec = {
        "modules": modules,
        "allowed": ["guardrails.evolution." + name for name in allowed_domain],
        "blocked": blocked,
        "controls": ["sqlalchemy", "psycopg", "guardrails.evolution.storage"],
        "exercise": exercise,
    }
    environment = dict(os.environ)
    # Isolated Python ignores PYTHONPATH and user site, so the product root is
    # passed explicitly and no existing pytest process imports can satisfy it.
    completed = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, str(MVP), json.dumps(spec)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert completed.returncode == 0, "fresh process violated dependency boundary"
    assert json.loads(completed.stdout) == {
        "imported": modules,
        "forbidden_loaded": False,
    }


def test_pure_domain_and_compiler_import_without_web_database_or_network_clients():
    paths = [MVP / "guardrails/evolution" / (name + ".py") for name in DOMAIN]
    own = tuple("guardrails.evolution." + name for name in DOMAIN)
    violations = []
    for path in paths:
        for target in imports(path):
            root = target.partition(".")[0]
            if under(target, INFRASTRUCTURE + NETWORK_STDLIB + ASSEMBLY_ONLY) or not (
                root in sys.stdlib_module_names
                or root == "pydantic"
                or under(target, own)
            ):
                violations.append((path.name, target))
    assert not violations, (
        "domain imported a transport, persistence or assembly dependency"
    )
    isolated_imports(
        ["guardrails.evolution." + name for name in DOMAIN],
        DOMAIN,
        list(INFRASTRUCTURE + ASSEMBLY_ONLY),
        """
from guardrails.evolution.models import ContentPart, Finding
from guardrails.evolution.transformations import transform_text, validate_transformed_parts
original = (ContentPart(part_id='p', kind='text', role='user', text='abc'),)
findings = (Finding(rule_id='pii', category='email', part_id='p', reason_code='pii_detected', evaluator_revision='pii-v2', start=0, end=3),)
result = transform_text(original, findings)
validate_transformed_parts(original, findings, result)
if result[0].text != '[REDACTED]' or original[0].text != 'abc':
    raise SystemExit('pure_transformation_failed')
""",
    )


def test_connector_public_contract_imports_without_repository_compiler_or_workers():
    paths = sorted((MVP / "connector").glob("evolution*.py"))
    assert {path.stem for path in paths} >= {
        "evolution",
        "evolution_routes",
        "evolution_gateway",
        "evolution_guardrail",
    }
    public = tuple("guardrails.evolution." + name for name in PUBLIC)
    violations = []
    for path in paths:
        for target in imports(path):
            if (
                target.startswith("guardrails.")
                and not under(target, public)
                or under(
                    target,
                    ("sqlalchemy", "psycopg", "psycopg2", "sqlite3") + ASSEMBLY_ONLY,
                )
            ):
                violations.append((path.name, target))
    assert not violations, "connector imported private policy or service infrastructure"
    isolated_imports(
        [
            "connector.evolution",
            "connector.evolution_routes",
            "connector.evolution_gateway",
            "guardrails.evolution.egress",
            "guardrails.evolution.transformations",
        ],
        PUBLIC,
        ["sqlalchemy", "psycopg", "psycopg2", "sqlite3", "litellm", *ASSEMBLY_ONLY],
        """
from connector.evolution import normalize_messages
parts = normalize_messages([{'role': 'user', 'content': 'hello'}], 'input')
if len(parts) != 1 or parts[0].text != 'hello' or parts[0].role != 'user':
    raise SystemExit('public_normalization_failed')
""",
    )


def test_product_modules_do_not_import_fixtures_runners_or_plan_documents():
    paths = sorted((MVP / "guardrails/evolution").rglob("*.py"))
    paths += sorted((MVP / "connector").glob("evolution*.py"))
    assert {path.stem for path in paths} >= {
        "models",
        "compiler",
        "governance",
        "storage",
        "runtime_api",
        "plugins",
        "evolution_gateway",
    }, "the test must inspect both service and connector product code"
    violations = [
        (str(path.relative_to(MVP)), target)
        for path in paths
        for target in imports(path)
        if under(target, ASSEMBLY_ONLY)
        or any(
            part == "fixtures" or part.startswith("test_") for part in target.split(".")
        )
    ]
    assert not violations, "product code imported test, deployment or plan assembly"
