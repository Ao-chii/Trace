from __future__ import annotations

import ast
from contextlib import contextmanager
import importlib
from pathlib import Path
import sys
from typing import Any


class ProbeCheckError(ValueError):
    pass


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _module_name_from_relative_file(file: str) -> str:
    path = Path(file)
    if path.is_absolute() or ".." in path.parts or path.suffix != ".py":
        raise ProbeCheckError(f"probe file is not a safe Python module path: {file}")
    return ".".join(path.with_suffix("").parts)


@contextmanager
def _probe_import_context(root: Path, module_name: str):
    root = root.resolve()
    root_str = str(root)
    top_module = module_name.split(".", 1)[0]
    saved_modules: dict[str, Any] = {}

    for name, module in list(sys.modules.items()):
        if name != top_module and not name.startswith(f"{top_module}."):
            continue
        module_file = getattr(module, "__file__", None)
        if module_file is None:
            saved_modules[name] = module
            del sys.modules[name]
            continue
        try:
            module_path = Path(module_file).resolve()
        except OSError as exc:
            raise ProbeCheckError(f"cannot resolve loaded probe module {name}: {module_file}") from exc
        if _is_relative_to(module_path, root):
            saved_modules[name] = module
            del sys.modules[name]
            continue
        raise ProbeCheckError(f"probe import would shadow loaded module {name} from {module_path}")

    inserted = False
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
        inserted = True
    try:
        yield
    finally:
        for name in list(sys.modules):
            if name == top_module or name.startswith(f"{top_module}."):
                del sys.modules[name]
        sys.modules.update(saved_modules)
        if inserted:
            try:
                sys.path.remove(root_str)
            except ValueError:
                pass


def _run_function_probe(root: Path, *, file: str, probe: str) -> Any:
    module_name = _module_name_from_relative_file(file)
    try:
        parsed = ast.parse(probe, mode="eval")
    except SyntaxError as exc:
        raise ProbeCheckError(f"function probe is not a valid expression: {probe}") from exc
    call = parsed.body
    if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
        raise ProbeCheckError(f"function probe must be a direct function call with constant args: {probe}")
    args = [_literal_probe_value(arg, probe=probe) for arg in call.args]
    kwargs = {kw.arg: _literal_probe_value(kw.value, probe=probe) for kw in call.keywords if kw.arg}
    if len(kwargs) != len(call.keywords):
        raise ProbeCheckError(f"function probe cannot use **kwargs expansion: {probe}")

    with _probe_import_context(root, module_name):
        module = importlib.import_module(module_name)
        func = getattr(module, call.func.id, None)
        if not callable(func):
            raise ProbeCheckError(f"function probe target is not callable: {call.func.id}")
        return func(*args, **kwargs)


def _literal_probe_value(node: ast.AST, *, probe: str) -> Any:
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError) as exc:
        raise ProbeCheckError(f"function probe arguments must be constants: {probe}") from exc


def _run_route_probe(root: Path, *, file: str, probe: str) -> Any:
    module_name = _module_name_from_relative_file(file)
    with _probe_import_context(root, module_name):
        module = importlib.import_module(module_name)
        app = getattr(module, "app", None)
        if app is None:
            raise ProbeCheckError(f"route probe module has no FastAPI app: {module_name}")
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            return client.get(probe).status_code


def _run_probe(root: Path, *, kind: str, file: str, probe: str) -> Any:
    if kind == "function":
        return _run_function_probe(root, file=file, probe=probe)
    if kind == "route":
        return _run_route_probe(root, file=file, probe=probe)
    raise ProbeCheckError(f"unsupported probe target kind: {kind}")


def check_variant_probe(
    *,
    clean_root: Path,
    variant_root: Path,
    ground_truth: dict,
    variant_id: str,
) -> dict | None:
    probe_meta = ground_truth.get("probe") if isinstance(ground_truth.get("probe"), dict) else ground_truth
    probe = probe_meta.get("probe")
    has_probe_metadata = any(key in probe_meta for key in ("probe", "clean_value", "buggy_value"))
    if not has_probe_metadata:
        return None

    patch = ((ground_truth.get("patch_artifact") or {}).get("patch") or {})
    required = {
        "target_kind": probe_meta.get("target_kind"),
        "probe": probe,
        "clean_value": probe_meta.get("clean_value"),
        "buggy_value": probe_meta.get("buggy_value"),
        "patch_file": patch.get("file"),
    }
    missing = []
    for key in ("target_kind", "probe"):
        if required[key] is None:
            missing.append(key)
    for key in ("clean_value", "buggy_value"):
        if key not in probe_meta:
            missing.append(key)
    if required["patch_file"] is None:
        missing.append("patch_file")
    if missing:
        raise ProbeCheckError(f"bug variant {variant_id} probe metadata is incomplete: {', '.join(missing)}")
    if required["clean_value"] == required["buggy_value"]:
        raise ProbeCheckError(f"bug variant {variant_id} probe clean_value and buggy_value must differ")

    clean_actual = _run_probe(
        clean_root,
        kind=str(required["target_kind"]),
        file=str(required["patch_file"]),
        probe=str(required["probe"]),
    )
    buggy_actual = _run_probe(
        variant_root,
        kind=str(required["target_kind"]),
        file=str(required["patch_file"]),
        probe=str(required["probe"]),
    )
    passed = clean_actual == required["clean_value"] and buggy_actual == required["buggy_value"]
    result = {
        "status": "passed" if passed else "failed",
        "target_kind": required["target_kind"],
        "probe": required["probe"],
        "clean_expected": required["clean_value"],
        "clean_actual": clean_actual,
        "buggy_expected": required["buggy_value"],
        "buggy_actual": buggy_actual,
    }
    if not passed:
        raise ProbeCheckError(f"bug variant {variant_id} probe check failed: {result}")
    return result
