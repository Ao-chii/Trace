# Reflection 契约的静态闸门：别让被审的人自己打分。
# 不信任模型自报的 lowered_assertion_strength，直接比对首轮/修复版 AST，
# 抓 pytest.skip/xfail、空洞断言、宽泛 except 吞异常、断言/测试数缩水。
from __future__ import annotations

import ast
import math
import re
from typing import Any, Sequence
from urllib.parse import parse_qs, urlsplit

_SKIP_NAMES = {"skip", "skipif", "xfail"}
_BROAD_EXC = {"Exception", "BaseException"}
_HTTP_VERBS = {"get", "post", "put", "delete", "patch", "options", "head"}
_EXCEPTION_BASES_ENV_KEY = "__trace_contract_guard_exception_bases__"
_RESPONSE_MODEL_LIST_CONTAINERS = {"list", "List", "Sequence"}
_PYTEST_BUILTIN_FIXTURES = {
    "cache",
    "capsys",
    "capsysbinary",
    "capfd",
    "capfdbinary",
    "doctest_namespace",
    "monkeypatch",
    "pytestconfig",
    "record_property",
    "record_testsuite_property",
    "record_xml_attribute",
    "recwarn",
    "tmp_path",
    "tmp_path_factory",
    "tmpdir",
    "tmpdir_factory",
}


def check_reflection_contract(old_content: str, new_content: str) -> list[str]:
    """静态比对首轮/修复版，返回违规原因列表（空 = 通过）。修复版解析失败也算违规。"""
    violations: list[str] = []
    try:
        new_tree = ast.parse(new_content or "")
    except SyntaxError as e:
        return [f"修复版无法解析为 Python：{e}"]
    try:
        old_tree = ast.parse(old_content or "")
    except SyntaxError:
        old_tree = None  # 首轮本身语法错，只能单看修复版

    if _has_skip_or_xfail(new_tree):
        violations.append("引入 pytest.skip/xfail 跳过测试")
    if _has_trivial_assert(new_tree):
        violations.append("出现空洞断言（assert True / assert 1 / ... or True 之类）")
    if _has_broad_except(new_tree):
        violations.append("用宽泛 except 吞掉失败（bare except / except Exception）")

    # 绝对下限：不管首轮能否解析，修复版必须有测试函数 + 至少一个断言式检查。
    # 堵「首轮 collection error → old_tree=None → 缩水检查被跳过 → 改成空测试骗绿」的洞。
    if _count_tests(new_tree) == 0:
        violations.append("修复版没有任何测试函数")
    if _count_checks(new_tree) == 0:
        violations.append("修复版没有任何断言/pytest.raises（疑似空测试）")

    # 相对首轮不缩水；首轮语法错（collection error）无法 AST 比对，靠上面的绝对下限兜底
    if old_tree is not None:
        if _count_asserts(new_tree) < _count_asserts(old_tree):
            violations.append("断言数量较首轮减少")
        if _count_tests(new_tree) < _count_tests(old_tree):
            violations.append("测试函数数量较首轮减少")
    return violations


def check_generation_contract(
    content: str,
    declared_cases: Sequence[Any],
    *,
    target_type: str | None = None,
    target_ref: str | None = None,
    allowed_request_fields: Sequence[str] | None = None,
    allowed_fixtures: Sequence[str] | None = None,
    source_context: str | None = None,
) -> list[str]:
    """校验生成测试本身是否有基本测试价值和目标绑定。"""
    violations: list[str] = []
    try:
        tree = ast.parse(content or "")
    except SyntaxError as e:
        return [f"生成测试无法解析为 Python：{e}"]

    parsed_names = set(_test_names(tree))
    declared_by_name = {
        _base_test_name(_field(case, "test_name")): case for case in declared_cases if _field(case, "test_name")
    }

    if _has_skip_or_xfail(tree):
        violations.append("生成测试引入 pytest.skip/xfail 跳过测试")
    if _has_trivial_assert(tree):
        violations.append("生成测试出现空洞断言（assert True / assert 1 / ... or True 之类）")
    if _has_broad_except(tree):
        violations.append("生成测试用宽泛 except 吞掉失败（bare except / except Exception）")
    if _count_tests(tree) == 0:
        violations.append("生成测试没有任何测试函数")
    if _count_checks(tree) == 0:
        violations.append("生成测试没有任何断言/pytest.raises（疑似空测试）")
    route_json_without_schema = (
        _route_tests_with_json_body(tree, declared_by_name)
        if allowed_request_fields is not None and not list(allowed_request_fields)
        else []
    )
    if route_json_without_schema:
        violations.append("请求 JSON 缺少模型字段证据：" + ", ".join(route_json_without_schema))
    unknown_fields = _unknown_json_body_fields(tree, allowed_request_fields or [])
    if unknown_fields:
        violations.append("请求 JSON 字段缺少模型证据：" + ", ".join(unknown_fields))
    unknown_fixtures = _unknown_test_fixtures(tree, allowed_fixtures) if allowed_fixtures is not None else []
    if unknown_fixtures:
        violations.append("测试函数 fixture 缺少项目证据：" + ", ".join(unknown_fixtures))
    shadowed_targets = _shadowed_target_definitions(tree, declared_by_name, target_type, target_ref)
    if shadowed_targets:
        violations.append("生成测试重新定义被测目标：" + ", ".join(shadowed_targets))

    if not declared_by_name:
        violations.append("生成测试缺少 cases 元数据声明")
    missing = sorted(parsed_names - set(declared_by_name))
    extra = sorted(set(declared_by_name) - parsed_names)
    if missing:
        violations.append("真实测试函数缺少 cases 声明：" + ", ".join(missing))
    if extra:
        violations.append("cases 声明未对应真实测试函数：" + ", ".join(extra))
    weak_route_oracles = _success_status_only_route_tests(tree, declared_by_name)
    if weak_route_oracles:
        violations.append("路由测试缺少业务 oracle（只有 2xx status_code 断言）：" + ", ".join(weak_route_oracles))
    empty_path_param_routes = _empty_path_param_route_tests(tree, source_context or "")
    if empty_path_param_routes:
        violations.append("路由测试使用空 path 参数，无法命中目标 handler：" + ", ".join(empty_path_param_routes))
    source_oracle_violations = _source_backed_oracle_violations(tree, declared_by_name, source_context or "")
    if source_oracle_violations:
        violations.extend(source_oracle_violations)
    response_model_violations = _route_response_model_field_violations(tree, source_context or "")
    if response_model_violations:
        violations.extend(response_model_violations)
    route_oracle_violations = _route_response_oracle_violations(tree, source_context or "")
    if route_oracle_violations:
        violations.extend(route_oracle_violations)

    for name in sorted(parsed_names & set(declared_by_name)):
        case = declared_by_name[name]
        if not str(_field(case, "assertion_summary") or "").strip():
            violations.append(f"{name} 缺少 assertion_summary")
        target_function = str(_field(case, "target_function") or "").strip()
        target_route = str(_field(case, "target_route") or "").strip()
        if not target_function and not target_route:
            violations.append(f"{name} 缺少 target_function/target_route 绑定")
            continue
        if not _target_matches(target_type, target_ref, target_function, target_route):
            violations.append(f"{name} 的目标绑定不匹配 plan item：{target_type}:{target_ref}")

    return violations


def _field(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _base_test_name(name: Any) -> str:
    base = str(name).split("::")[-1].split("[", 1)[0].strip()
    if "." in base and base.rsplit(".", 1)[-1].startswith("test"):
        return base.rsplit(".", 1)[-1]
    return base


def _test_names(tree) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            names.append(node.name)
    return names


def _target_matches(target_type: str | None, target_ref: str | None, target_function: str, target_route: str) -> bool:
    ref = (target_ref or "").strip()
    if not ref or ref == "all":
        return True
    if target_type == "function":
        return target_function.rsplit(".", 1)[-1] == ref.rsplit(".", 1)[-1]
    if target_type == "route":
        route_ref = _route_path_without_method(ref)
        route_target = _route_path_without_method(target_route)
        return route_target == route_ref or target_function.rsplit(".", 1)[-1] == ref.rsplit(".", 1)[-1]
    return True


def _route_path_without_method(value: str) -> str:
    normalized = value.strip()
    if normalized.lower().startswith("route:"):
        normalized = normalized.split(":", 1)[1].strip()
    parts = normalized.split(maxsplit=1)
    if len(parts) == 2 and parts[0].upper() in {"GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"}:
        return parts[1].strip()
    return normalized


def _empty_path_param_route_tests(tree: ast.AST, source_context: str) -> list[str]:
    empty_paths = _empty_path_param_routes(source_context)
    if not empty_paths:
        return []
    offenders: set[str] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call) or _last(node.func).lower() not in _HTTP_VERBS:
                continue
            if not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str) and first.value in empty_paths:
                offenders.add(fn.name)
    return sorted(offenders)


def _empty_path_param_routes(source_context: str) -> set[str]:
    routes: set[str] = set()
    for template in _route_templates_from_source_context(source_context):
        match = re.fullmatch(r"(.*/)\{[^/{}]+\}/?", template)
        if match:
            routes.add(match.group(1))
    return routes


def _route_templates_from_source_context(source_context: str) -> set[str]:
    chunks = re.findall(r"```(?:python)?\s*(.*?)```", source_context, flags=re.DOTALL)
    if not chunks:
        chunks = [source_context]
    routes: set[str] = set()
    for chunk in chunks:
        try:
            tree = ast.parse(chunk)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                call = dec if isinstance(dec, ast.Call) else None
                if call is None or _last(call.func).lower() not in _HTTP_VERBS:
                    continue
                if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
                    routes.add(call.args[0].value)
    return routes


def _shadowed_target_definitions(
    tree: ast.AST,
    declared_by_name: dict[str, Any],
    target_type: str | None,
    target_ref: str | None,
) -> list[str]:
    targets: set[str] = set()
    if target_type == "function" and target_ref:
        targets.add(str(target_ref).rsplit(".", 1)[-1])
    for case in declared_by_name.values():
        target_function = str(_field(case, "target_function") or "").strip()
        if target_function:
            targets.add(target_function.rsplit(".", 1)[-1])
    if not targets:
        return []

    shadowed: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("test"):
            if node.name in targets:
                shadowed.add(node.name)
    return sorted(shadowed)


class _EvalUnknown(Exception):
    pass


class _EvalRaised(Exception):
    def __init__(
        self,
        *,
        exc_type: str | None = None,
        message: Any = None,
        status_code: int | None = None,
        detail: Any = None,
    ) -> None:
        super().__init__()
        self.exc_type = exc_type
        self.message = None if message is None else str(message)
        self.status_code = status_code
        self.detail = detail


_NO_RETURN = object()


class _RouteResponse:
    def __init__(self, *, status_code: int, json_body: Any) -> None:
        self.status_code = status_code
        self.json_body = json_body


def _source_backed_oracle_violations(
    tree: ast.AST,
    declared_by_name: dict[str, Any],
    source_context: str,
) -> list[str]:
    if not source_context.strip():
        return []
    functions = _source_functions(source_context)
    if not functions:
        return []
    exception_bases = _source_exception_bases(source_context)
    source_env = _source_global_env(source_context, functions, exception_bases)

    violations: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for base_env in _parametrize_envs(fn):
            env = dict(source_env)
            env.update(base_env)
            tainted_names: set[str] = set()
            for stmt in fn.body:
                _remember_static_assignment(stmt, functions, env, tainted_names)
                if isinstance(stmt, ast.Assert):
                    if not _expr_uses_source_value(stmt.test, functions, tainted_names):
                        continue
                    ok = _try_eval_assert(stmt.test, functions, env)
                    if ok is False:
                        violations.append(f"{fn.name} 的 oracle 与目标源码行为不一致")
                elif isinstance(stmt, ast.With):
                    pytest_raises_violation = _pytest_raises_source_violation(stmt, functions, env)
                    if pytest_raises_violation:
                        violations.append(f"{fn.name} {pytest_raises_violation}")
    return sorted(set(violations))


def _route_response_oracle_violations(tree: ast.AST, source_context: str) -> list[str]:
    if not source_context.strip():
        return []
    functions = _source_functions(source_context)
    exception_bases = _source_exception_bases(source_context)
    source_env = _source_global_env(source_context, functions, exception_bases)
    routes = _route_handlers_from_source_context(source_context)
    if not functions or not routes:
        return []

    violations: set[str] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not any(isinstance(node, ast.Call) and _last(node.func).lower() in _HTTP_VERBS for node in ast.walk(fn)):
            continue
        for base_env in _parametrize_envs(fn):
            env = dict(base_env)
            global_env = dict(source_env)
            tainted_names: set[str] = set()
            for stmt in fn.body:
                _remember_monkeypatch_global(stmt, functions, env, global_env)
                if _remember_route_response_assignment(stmt, routes, functions, env, global_env, tainted_names):
                    continue
                _remember_route_static_assignment(stmt, functions, env, tainted_names)
                if isinstance(stmt, ast.Assert) and _expr_uses_route_value(stmt.test, tainted_names):
                    ok = _try_eval_assert(stmt.test, functions, env)
                    if ok is False:
                        violations.add(f"{fn.name} 的路由响应 oracle 与目标源码行为不一致")
                        break
    return sorted(violations)


def _route_response_model_field_violations(tree: ast.AST, source_context: str) -> list[str]:
    route_models = _route_response_model_fields(source_context)
    if not route_models:
        return []

    violations: set[str] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        response_models: dict[str, tuple[set[str], str]] = {}
        json_aliases: dict[str, str] = {}
        success_responses: set[str] = set()
        field_reads: list[tuple[str, str, str]] = []

        for stmt in ast.walk(fn):
            if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                targets = list(stmt.targets) if isinstance(stmt, ast.Assign) else [stmt.target]
                value = stmt.value
                if isinstance(value, ast.Call):
                    model = _response_model_fields_for_call(value, route_models)
                    if model is not None:
                        for target in targets:
                            if isinstance(target, ast.Name):
                                response_models[target.id] = model
                    response_name = _json_response_name(value)
                    if response_name:
                        for target in targets:
                            if isinstance(target, ast.Name):
                                json_aliases[target.id] = response_name
            if isinstance(stmt, ast.Assert):
                success_responses.update(_success_status_response_names(stmt.test))
            for subscript in [node for node in ast.walk(stmt) if isinstance(node, ast.Subscript)]:
                field_read = _response_field_read(subscript, json_aliases)
                if field_read is not None:
                    field_reads.append(field_read)

        for response_name, read_shape, field in field_reads:
            model = response_models.get(response_name)
            if model is None or response_name not in success_responses:
                continue
            allowed, model_shape = model
            if read_shape != model_shape:
                continue
            if field not in allowed:
                violations.add(f"{fn.name} 响应字段缺少模型证据：{field}")
    return sorted(violations)


def _route_response_model_fields(source_context: str) -> dict[tuple[str, str], tuple[set[str], str]]:
    chunks = re.findall(r"```(?:python)?\s*(.*?)```", source_context, flags=re.DOTALL)
    if not chunks:
        chunks = [source_context]

    fields_by_model: dict[str, set[str]] = {}
    route_models: dict[tuple[str, str], tuple[set[str], str]] = {}
    parsed: list[ast.AST] = []
    for chunk in chunks:
        try:
            tree = ast.parse(chunk)
        except SyntaxError:
            continue
        parsed.append(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and _is_pydantic_model_class(node):
                fields_by_model[node.name] = _class_field_names(node)

    for tree in parsed:
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                call = dec if isinstance(dec, ast.Call) else None
                if call is None or _last(call.func).lower() not in _HTTP_VERBS:
                    continue
                route_path = _route_path_from_decorator(call)
                response_model = _response_model_ref(call)
                if route_path is None or response_model is None:
                    continue
                model_name, model_shape = response_model
                fields = fields_by_model.get(model_name.rsplit(".", 1)[-1])
                if fields:
                    route_models[(_last(call.func).lower(), route_path)] = (fields, model_shape)
    return route_models


def _is_pydantic_model_class(node: ast.ClassDef) -> bool:
    return any(_last(base) == "BaseModel" for base in node.bases)


def _class_field_names(node: ast.ClassDef) -> set[str]:
    fields: set[str] = set()
    for item in node.body:
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name) and not item.target.id.startswith("_"):
            fields.add(item.target.id)
            fields.update(_pydantic_field_aliases(item.value))
    return fields


def _pydantic_field_aliases(node: ast.AST | None) -> set[str]:
    if not isinstance(node, ast.Call) or _last(node.func) != "Field":
        return set()
    aliases: set[str] = set()
    for kw in node.keywords:
        if kw.arg in {"alias", "serialization_alias"} and isinstance(kw.value, ast.Constant):
            if isinstance(kw.value.value, str) and kw.value.value:
                aliases.add(kw.value.value)
    return aliases


def _route_path_from_decorator(call: ast.Call) -> str | None:
    if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
        return call.args[0].value
    for kw in call.keywords:
        if kw.arg == "path" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    return None


def _response_model_ref(call: ast.Call) -> tuple[str, str] | None:
    for kw in call.keywords:
        if kw.arg == "response_model":
            return _response_model_ref_from_node(kw.value)
    return None


def _response_model_ref_from_node(node: ast.AST) -> tuple[str, str] | None:
    model_name = _attr_chain(node)
    if model_name:
        return model_name, "object"
    if isinstance(node, ast.Subscript) and _last(node.value) in _RESPONSE_MODEL_LIST_CONTAINERS:
        item_model_name = _attr_chain(node.slice)
        if item_model_name:
            return item_model_name, "list"
    return None


def _response_model_fields_for_call(
    call: ast.Call,
    route_models: dict[tuple[str, str], tuple[set[str], str]],
) -> tuple[set[str], str] | None:
    method = _last(call.func).lower()
    if method not in _HTTP_VERBS or not call.args:
        return None
    path_node = call.args[0]
    if not isinstance(path_node, ast.Constant) or not isinstance(path_node.value, str):
        return None
    path = urlsplit(path_node.value).path or path_node.value
    for (route_method, template), fields in route_models.items():
        if route_method == method and _match_route_params(template, path) is not None:
            return fields
    return None


def _json_response_name(call: ast.Call) -> str | None:
    if (
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "json"
        and isinstance(call.func.value, ast.Name)
    ):
        return call.func.value.id
    return None


def _success_status_response_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for compare in [child for child in ast.walk(node) if isinstance(child, ast.Compare)]:
        operands = [compare.left, *compare.comparators]
        status_names = [
            operand.value.id
            for operand in operands
            if isinstance(operand, ast.Attribute)
            and operand.attr == "status_code"
            and isinstance(operand.value, ast.Name)
        ]
        success_codes = [
            operand.value
            for operand in operands
            if isinstance(operand, ast.Constant) and isinstance(operand.value, int) and 200 <= operand.value < 300
        ]
        if status_names and success_codes:
            names.update(status_names)
    return names


def _constant_subscript_key(node: ast.Subscript) -> str | None:
    key = node.slice
    if isinstance(key, ast.Constant) and isinstance(key.value, str):
        return key.value
    return None


def _constant_subscript_index(node: ast.Subscript) -> int | None:
    key = node.slice
    if isinstance(key, ast.Constant) and isinstance(key.value, int) and not isinstance(key.value, bool):
        return key.value
    return None


def _response_name_for_json_subscript(node: ast.Subscript) -> str | None:
    value = node.value
    if isinstance(value, ast.Call):
        return _json_response_name(value)
    return None


def _response_field_read(
    node: ast.Subscript,
    json_aliases: dict[str, str],
) -> tuple[str, str, str] | None:
    field = _constant_subscript_key(node)
    if not field:
        return None
    response_name = _response_name_for_json_subscript(node)
    if response_name:
        return response_name, "object", field
    if isinstance(node.value, ast.Name) and node.value.id in json_aliases:
        return json_aliases[node.value.id], "object", field
    if isinstance(node.value, ast.Subscript) and _constant_subscript_index(node.value) is not None:
        item = node.value
        response_name = _response_name_for_json_subscript(item)
        if response_name:
            return response_name, "list", field
        if isinstance(item.value, ast.Name) and item.value.id in json_aliases:
            return json_aliases[item.value.id], "list", field
    return None


def _route_handlers_from_source_context(source_context: str) -> dict[str, str]:
    chunks = re.findall(r"```(?:python)?\s*(.*?)```", source_context, flags=re.DOTALL)
    if not chunks:
        chunks = [source_context]
    routes: dict[str, str] = {}
    for chunk in chunks:
        try:
            tree = ast.parse(chunk)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                call = dec if isinstance(dec, ast.Call) else None
                if call is None or _last(call.func).lower() not in _HTTP_VERBS:
                    continue
                if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
                    routes.setdefault(call.args[0].value, node.name)
    return routes


def _parametrize_envs(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[dict[str, Any]]:
    envs: list[dict[str, Any]] = [{}]
    for dec in fn.decorator_list:
        call = dec if isinstance(dec, ast.Call) else None
        if call is None or _last(call.func) != "parametrize" or len(call.args) < 2:
            continue
        names = _parametrize_names(call.args[0])
        if not names:
            continue
        try:
            raw_cases = _eval_expr(call.args[1], {}, {})
        except (_EvalUnknown, _EvalRaised):
            continue
        if not isinstance(raw_cases, (list, tuple)):
            continue
        next_envs: list[dict[str, Any]] = []
        for env in envs:
            for raw_case in raw_cases:
                values = raw_case if isinstance(raw_case, tuple) else (raw_case,)
                if len(values) != len(names):
                    continue
                merged = dict(env)
                merged.update(zip(names, values))
                next_envs.append(merged)
        if next_envs:
            envs = next_envs
    return envs


def _parametrize_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [part.strip() for part in node.value.split(",") if part.strip()]
    if isinstance(node, (ast.List, ast.Tuple)):
        names: list[str] = []
        for item in node.elts:
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                names.append(item.value)
        return names
    return []


def _remember_monkeypatch_global(
    stmt: ast.stmt,
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    env: dict[str, Any],
    global_env: dict[str, Any],
) -> None:
    for call in [node for node in ast.walk(stmt) if isinstance(node, ast.Call)]:
        if _last(call.func) != "setattr" or len(call.args) < 2:
            continue
        target = call.args[0]
        if not isinstance(target, ast.Constant) or not isinstance(target.value, str):
            continue
        name = target.value.rsplit(".", 1)[-1]
        if not name:
            continue
        try:
            global_env[name] = _eval_expr(call.args[1], functions, env)
        except (_EvalUnknown, _EvalRaised):
            continue


def _remember_route_response_assignment(
    stmt: ast.stmt,
    routes: dict[str, str],
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    env: dict[str, Any],
    global_env: dict[str, Any],
    tainted_names: set[str],
) -> bool:
    targets: list[ast.expr] = []
    value: ast.AST | None = None
    if isinstance(stmt, ast.Assign):
        targets = list(stmt.targets)
        value = stmt.value
    elif isinstance(stmt, ast.AnnAssign):
        targets = [stmt.target]
        value = stmt.value
    if value is None or not isinstance(value, ast.Call) or _last(value.func).lower() not in _HTTP_VERBS:
        return False
    try:
        response = _eval_route_response_call(value, routes, functions, env, global_env)
    except (_EvalUnknown, _EvalRaised):
        _clear_assigned_names(targets, env, tainted_names)
        return True
    for target in targets:
        if isinstance(target, ast.Name):
            env[target.id] = response
            tainted_names.add(target.id)
    return True


def _remember_route_static_assignment(
    stmt: ast.stmt,
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    env: dict[str, Any],
    tainted_names: set[str],
) -> None:
    targets: list[ast.expr] = []
    value: ast.AST | None = None
    if isinstance(stmt, ast.Assign):
        targets = list(stmt.targets)
        value = stmt.value
    elif isinstance(stmt, ast.AnnAssign):
        targets = [stmt.target]
        value = stmt.value
    if value is None:
        return
    try:
        evaluated = _eval_expr(value, functions, env)
    except (_EvalUnknown, _EvalRaised):
        _clear_assigned_names(targets, env, tainted_names)
        return
    tainted = _expr_uses_route_value(value, tainted_names)
    for target in targets:
        if isinstance(target, ast.Name):
            env[target.id] = evaluated
            if tainted:
                tainted_names.add(target.id)


def _eval_route_response_call(
    call: ast.Call,
    routes: dict[str, str],
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    env: dict[str, Any],
    global_env: dict[str, Any],
) -> _RouteResponse:
    if not call.args:
        raise _EvalUnknown()
    path = _eval_expr(call.args[0], functions, env)
    if not isinstance(path, str):
        raise _EvalUnknown()
    parsed = urlsplit(path)
    route_path = parsed.path or path
    query_params: dict[str, Any] = {}
    for key, values in parse_qs(parsed.query).items():
        if values:
            query_params[key] = _coerce_query_value(values[-1])
    for kw in call.keywords:
        if kw.arg == "params":
            value = _eval_expr(kw.value, functions, env)
            if isinstance(value, dict):
                query_params.update(value)
    for template, handler_name in routes.items():
        route_params = _match_route_params(template, route_path)
        if route_params is None or handler_name not in functions:
            continue
        kwargs = {**route_params, **query_params}
        try:
            body = _eval_source_function(functions[handler_name], [], kwargs, functions, global_env)
        except _EvalRaised as exc:
            if exc.status_code is None:
                raise
            return _RouteResponse(status_code=exc.status_code, json_body={"detail": exc.detail})
        return _RouteResponse(status_code=200, json_body=body)
    raise _EvalUnknown()


def _coerce_query_value(value: str) -> Any:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _clear_assigned_names(targets: Sequence[ast.expr], env: dict[str, Any], tainted_names: set[str]) -> None:
    for target in targets:
        if isinstance(target, ast.Name):
            env.pop(target.id, None)
            tainted_names.discard(target.id)


def _match_route_params(template: str, path: str) -> dict[str, str] | None:
    template_parts = template.strip("/").split("/") if template.strip("/") else []
    path_parts = path.strip("/").split("/") if path.strip("/") else []
    if len(template_parts) != len(path_parts):
        return None
    params: dict[str, str] = {}
    for template_part, path_part in zip(template_parts, path_parts):
        match = re.fullmatch(r"\{([^/{}]+)\}", template_part)
        if match:
            if path_part == "":
                return None
            params[match.group(1)] = path_part
            continue
        if template_part != path_part:
            return None
    return params


def _expr_uses_route_value(node: ast.AST, tainted_names: set[str]) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id in tainted_names:
            return True
        if (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == "json"
            and isinstance(child.func.value, ast.Name)
            and child.func.value.id in tainted_names
        ):
            return True
    return False


def _source_functions(source_context: str) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    chunks = re.findall(r"```(?:python)?\s*(.*?)```", source_context, flags=re.DOTALL)
    if not chunks:
        chunks = [source_context]
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for chunk in chunks:
        try:
            tree = ast.parse(chunk)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.setdefault(node.name, node)
    return functions


def _source_exception_bases(source_context: str) -> dict[str, set[str]]:
    chunks = re.findall(r"```(?:python)?\s*(.*?)```", source_context, flags=re.DOTALL)
    if not chunks:
        chunks = [source_context]
    direct: dict[str, set[str]] = {}
    for chunk in chunks:
        try:
            tree = ast.parse(chunk)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases: set[str] = set()
            for base in node.bases:
                name = _attr_chain(base)
                if not name:
                    continue
                bases.add(name)
                bases.add(name.rsplit(".", 1)[-1])
            if bases:
                direct.setdefault(node.name, set()).update(bases)
    return {name: _expanded_exception_bases(name, direct) for name in direct}


def _expanded_exception_bases(name: str, direct: dict[str, set[str]]) -> set[str]:
    expanded: set[str] = set()
    stack = list(direct.get(name, set()))
    while stack:
        base = stack.pop()
        if base in expanded:
            continue
        expanded.add(base)
        short = base.rsplit(".", 1)[-1]
        stack.extend(direct.get(base, set()))
        if short != base:
            stack.extend(direct.get(short, set()))
    return expanded


def _eval_base_env(exception_bases: dict[str, set[str]]) -> dict[str, Any]:
    return {_EXCEPTION_BASES_ENV_KEY: exception_bases} if exception_bases else {}


def _source_global_env(
    source_context: str,
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    exception_bases: dict[str, set[str]],
) -> dict[str, Any]:
    env = _eval_base_env(exception_bases)
    chunks = re.findall(r"```(?:python)?\s*(.*?)```", source_context, flags=re.DOTALL)
    if not chunks:
        chunks = [source_context]
    for chunk in chunks:
        try:
            tree = ast.parse(chunk)
        except SyntaxError:
            continue
        for node in getattr(tree, "body", []):
            targets: list[ast.expr] = []
            value: ast.AST | None = None
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
                value = node.value
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
                value = node.value
            if value is None:
                continue
            try:
                evaluated = _eval_expr(value, functions, env)
            except (_EvalUnknown, _EvalRaised):
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    env[target.id] = evaluated
    return env

def _exception_bases_from_env(env: dict[str, Any]) -> dict[str, set[str]]:
    raw = env.get(_EXCEPTION_BASES_ENV_KEY)
    return raw if isinstance(raw, dict) else {}


def _remember_static_assignment(
    stmt: ast.stmt,
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    env: dict[str, Any],
    tainted_names: set[str],
) -> None:
    targets: list[ast.expr] = []
    value: ast.AST | None = None
    if isinstance(stmt, ast.Assign):
        targets = list(stmt.targets)
        value = stmt.value
    elif isinstance(stmt, ast.AnnAssign):
        targets = [stmt.target]
        value = stmt.value
    if value is None:
        return
    try:
        evaluated = _eval_expr(value, functions, env)
    except (_EvalUnknown, _EvalRaised):
        return
    tainted = _expr_uses_source_value(value, functions, tainted_names)
    for target in targets:
        if isinstance(target, ast.Name):
            env[target.id] = evaluated
            if tainted:
                tainted_names.add(target.id)


def _try_eval_assert(
    node: ast.AST,
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    env: dict[str, Any],
) -> bool | None:
    try:
        value = _eval_expr(node, functions, env)
    except (_EvalUnknown, _EvalRaised):
        return None
    if isinstance(value, bool):
        return value
    return bool(value)


def _pytest_raises_source_violation(
    stmt: ast.stmt,
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    env: dict[str, Any],
) -> str | None:
    if not isinstance(stmt, ast.With):
        return None
    raises_call = next(
        (
            item.context_expr
            for item in stmt.items
            if isinstance(item.context_expr, ast.Call) and _last(item.context_expr.func) == "raises"
        ),
        None,
    )
    if raises_call is None:
        return None
    expected_exceptions = _pytest_raises_expected_exceptions(raises_call)
    expected_match = _pytest_raises_match_pattern(raises_call, functions, env)
    for inner in stmt.body:
        for call in [node for node in ast.walk(inner) if isinstance(node, ast.Call)]:
            if _last(call.func) not in functions:
                continue
            try:
                _eval_expr(call, functions, env)
            except _EvalRaised as exc:
                if (
                    expected_exceptions
                    and exc.exc_type
                    and not _exception_type_matches(
                        expected_exceptions,
                        exc.exc_type,
                        _exception_bases_from_env(env),
                    )
                ):
                    return "预期异常类型与目标源码不一致"
                if expected_match and exc.message is not None:
                    try:
                        if re.search(expected_match, exc.message) is None:
                            return "预期异常消息与目标源码不一致"
                    except re.error:
                        return None
                return None
            except _EvalUnknown:
                continue
            return "预期异常但目标源码没有可见 raise 证据"
    return None


def _pytest_raises_expected_exceptions(call: ast.Call) -> set[str]:
    if not call.args:
        return set()
    return _exception_names(call.args[0])


def _exception_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Tuple):
        names: set[str] = set()
        for item in node.elts:
            names.update(_exception_names(item))
        return names
    name = _attr_chain(node)
    if not name:
        return set()
    return {name, name.rsplit(".", 1)[-1]}


def _pytest_raises_match_pattern(
    call: ast.Call,
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    env: dict[str, Any],
) -> str | None:
    for kw in call.keywords:
        if kw.arg != "match":
            continue
        try:
            pattern = _eval_expr(kw.value, functions, env)
        except (_EvalUnknown, _EvalRaised):
            return None
        return pattern if isinstance(pattern, str) else None
    return None


def _exception_type_matches(
    expected: set[str],
    actual: str,
    exception_bases: dict[str, set[str]] | None = None,
) -> bool:
    actual_names = {actual, actual.rsplit(".", 1)[-1]}
    if expected & actual_names:
        return True
    lineage = set(actual_names)
    for name in list(actual_names):
        lineage.update(_expanded_exception_bases(name, exception_bases or {}))
    if expected & lineage:
        return True
    return bool(expected & {"Exception", "BaseException"})


def _expr_uses_source_value(
    node: ast.AST,
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    tainted_names: set[str],
) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and _last(child.func) in functions:
            return True
        if isinstance(child, ast.Name) and child.id in tainted_names:
            return True
    return False


def _eval_source_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    args: list[Any],
    kwargs: dict[str, Any],
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    global_env: dict[str, Any] | None = None,
) -> Any:
    if isinstance(node, ast.AsyncFunctionDef):
        raise _EvalUnknown()
    arg_nodes = list(node.args.args)
    defaults = list(node.args.defaults)
    if len(args) > len(arg_nodes):
        raise _EvalUnknown()

    env: dict[str, Any] = dict(global_env or {})
    for arg_node, value in zip(arg_nodes, args):
        env[arg_node.arg] = value

    default_offset = len(arg_nodes) - len(defaults)
    for index, arg_node in enumerate(arg_nodes[len(args) :], start=len(args)):
        if arg_node.arg in kwargs:
            env[arg_node.arg] = kwargs.pop(arg_node.arg)
            continue
        default_index = index - default_offset
        if default_index < 0:
            raise _EvalUnknown()
        env[arg_node.arg] = _eval_expr(defaults[default_index], functions, env)
    if kwargs:
        raise _EvalUnknown()

    result = _exec_statements(node.body, functions, env)
    if result is _NO_RETURN:
        raise _EvalUnknown()
    return result


def _exec_statements(
    statements: Sequence[ast.stmt],
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    env: dict[str, Any],
) -> Any:
    for stmt in statements:
        if isinstance(stmt, ast.Return):
            return _eval_expr(stmt.value, functions, env)
        if isinstance(stmt, ast.Raise):
            raise _eval_raise(stmt, functions, env)
        if isinstance(stmt, ast.If):
            branch = stmt.body if _eval_expr(stmt.test, functions, env) else stmt.orelse
            result = _exec_statements(branch, functions, dict(env))
            if result is not _NO_RETURN:
                return result
            continue
        if isinstance(stmt, ast.Try):
            result = _exec_try_statement(stmt, functions, env)
            if result is not _NO_RETURN:
                return result
            continue
        if isinstance(stmt, ast.Assign):
            value = _eval_expr(stmt.value, functions, env)
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    env[target.id] = value
            continue
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.value is not None:
            env[stmt.target.id] = _eval_expr(stmt.value, functions, env)
            continue
        if isinstance(stmt, ast.Expr):
            if isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
                continue
            _eval_expr(stmt.value, functions, env)
            continue
        if isinstance(stmt, ast.Pass):
            continue
        raise _EvalUnknown()
    return _NO_RETURN


def _exec_try_statement(
    stmt: ast.Try,
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    env: dict[str, Any],
) -> Any:
    result: Any = _NO_RETURN
    pending_raise: _EvalRaised | None = None
    try:
        result = _exec_statements(stmt.body, functions, env)
    except _EvalRaised as exc:
        handled = False
        for handler in stmt.handlers:
            if not _except_handler_matches(handler, exc, env):
                continue
            handled = True
            handler_env = dict(env)
            if handler.name:
                handler_env[handler.name] = exc
            try:
                result = _exec_statements(handler.body, functions, handler_env)
            except _EvalRaised as handler_exc:
                pending_raise = handler_exc
                result = _NO_RETURN
            break
        if not handled:
            pending_raise = exc
    else:
        if result is _NO_RETURN and stmt.orelse:
            try:
                result = _exec_statements(stmt.orelse, functions, env)
            except _EvalRaised as else_exc:
                pending_raise = else_exc

    if stmt.finalbody:
        try:
            final_result = _exec_statements(stmt.finalbody, functions, env)
        except _EvalRaised as final_exc:
            raise final_exc
        if final_result is not _NO_RETURN:
            return final_result

    if pending_raise is not None:
        raise pending_raise
    return result


def _except_handler_matches(handler: ast.ExceptHandler, exc: _EvalRaised, env: dict[str, Any]) -> bool:
    if handler.type is None:
        return True
    expected = _exception_names(handler.type)
    if not expected or not exc.exc_type:
        return False
    return _exception_type_matches(expected, exc.exc_type, _exception_bases_from_env(env))


def _eval_raise(
    stmt: ast.Raise,
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    env: dict[str, Any],
) -> _EvalRaised:
    exc = stmt.exc
    if isinstance(exc, ast.Name):
        return _EvalRaised(exc_type=exc.id, message="")
    if not isinstance(exc, ast.Call):
        return _EvalRaised()
    exc_type = _last(exc.func)
    if exc_type != "HTTPException":
        message: Any = ""
        if exc.args:
            try:
                message = _eval_expr(exc.args[0], functions, env)
            except (_EvalUnknown, _EvalRaised):
                message = None
        return _EvalRaised(exc_type=exc_type or None, message=message)

    status_code: int | None = None
    detail: Any = None
    if exc.args:
        try:
            raw_status = _eval_expr(exc.args[0], functions, env)
        except (_EvalUnknown, _EvalRaised):
            raw_status = None
        if isinstance(raw_status, int):
            status_code = raw_status
    if len(exc.args) > 1:
        try:
            detail = _eval_expr(exc.args[1], functions, env)
        except (_EvalUnknown, _EvalRaised):
            detail = None
    for kw in exc.keywords:
        if kw.arg == "status_code":
            try:
                raw_status = _eval_expr(kw.value, functions, env)
            except (_EvalUnknown, _EvalRaised):
                raw_status = None
            if isinstance(raw_status, int):
                status_code = raw_status
        elif kw.arg == "detail":
            try:
                detail = _eval_expr(kw.value, functions, env)
            except (_EvalUnknown, _EvalRaised):
                detail = None
    return _EvalRaised(exc_type="HTTPException", message=detail, status_code=status_code, detail=detail)


def _eval_expr(
    node: ast.AST | None,
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    env: dict[str, Any],
) -> Any:
    if node is None:
        return None
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        raise _EvalUnknown()
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name):
            value = env.get(node.value.id)
            if isinstance(value, _RouteResponse) and node.attr == "status_code":
                return value.status_code
        raise _EvalUnknown()
    if isinstance(node, ast.UnaryOp):
        operand = _eval_expr(node.operand, functions, env)
        if isinstance(node.op, ast.USub):
            return -operand
        if isinstance(node.op, ast.UAdd):
            return +operand
        if isinstance(node.op, ast.Not):
            return not operand
        raise _EvalUnknown()
    if isinstance(node, ast.BoolOp):
        values = [_eval_expr(value, functions, env) for value in node.values]
        if isinstance(node.op, ast.And):
            return all(values)
        if isinstance(node.op, ast.Or):
            return any(values)
        raise _EvalUnknown()
    if isinstance(node, ast.BinOp):
        left = _eval_expr(node.left, functions, env)
        right = _eval_expr(node.right, functions, env)
        return _eval_binop(node.op, left, right)
    if isinstance(node, ast.Compare):
        left = _eval_expr(node.left, functions, env)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval_expr(comparator, functions, env)
            if not _compare_values(left, op, right):
                return False
            left = right
        return True
    if isinstance(node, ast.Call):
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "json"
            and isinstance(node.func.value, ast.Name)
            and isinstance(env.get(node.func.value.id), _RouteResponse)
        ):
            return env[node.func.value.id].json_body
        name = _last(node.func)
        args = [_eval_expr(arg, functions, env) for arg in node.args]
        kwargs = {kw.arg: _eval_expr(kw.value, functions, env) for kw in node.keywords if kw.arg}
        if name == "round":
            return round(*args, **kwargs)
        if name in functions:
            return _eval_source_function(
                functions[name],
                args,
                kwargs,
                functions,
                _eval_base_env(_exception_bases_from_env(env)),
            )
        raise _EvalUnknown()
    if isinstance(node, ast.Subscript):
        value = _eval_expr(node.value, functions, env)
        key = _eval_expr(node.slice, functions, env)
        try:
            return value[key]
        except (KeyError, IndexError, TypeError):
            raise _EvalUnknown() from None
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                parts.append(str(_eval_expr(value.value, functions, env)))
            else:
                raise _EvalUnknown()
        return "".join(parts)
    if isinstance(node, ast.Tuple):
        return tuple(_eval_expr(elt, functions, env) for elt in node.elts)
    if isinstance(node, ast.List):
        return [_eval_expr(elt, functions, env) for elt in node.elts]
    if isinstance(node, ast.Dict):
        return {
            _eval_expr(key, functions, env): _eval_expr(value, functions, env)
            for key, value in zip(node.keys, node.values)
            if key is not None
        }
    raise _EvalUnknown()


def _eval_binop(op: ast.operator, left: Any, right: Any) -> Any:
    if isinstance(op, ast.Add):
        return left + right
    if isinstance(op, ast.Sub):
        return left - right
    if isinstance(op, ast.Mult):
        return left * right
    if isinstance(op, ast.Div):
        return left / right
    if isinstance(op, ast.FloorDiv):
        return left // right
    if isinstance(op, ast.Mod):
        return left % right
    if isinstance(op, ast.Pow):
        return left**right
    raise _EvalUnknown()


def _compare_values(left: Any, op: ast.cmpop, right: Any) -> bool:
    if isinstance(op, ast.Eq):
        return _same_value(left, right)
    if isinstance(op, ast.NotEq):
        return not _same_value(left, right)
    if isinstance(op, ast.Lt):
        return left < right
    if isinstance(op, ast.LtE):
        return left <= right
    if isinstance(op, ast.Gt):
        return left > right
    if isinstance(op, ast.GtE):
        return left >= right
    if isinstance(op, ast.Is):
        return left is right
    if isinstance(op, ast.IsNot):
        return left is not right
    if isinstance(op, ast.In):
        return left in right
    if isinstance(op, ast.NotIn):
        return left not in right
    raise _EvalUnknown()


def _same_value(left: Any, right: Any) -> bool:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(float(left), float(right), rel_tol=1e-9, abs_tol=1e-9)
    return left == right


def _unknown_json_body_fields(tree: ast.AST, allowed_request_fields: Sequence[str]) -> list[str]:
    allowed = {str(field).strip() for field in allowed_request_fields if str(field).strip()}
    if not allowed:
        return []
    unknown: set[str] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        dict_vars = _dict_literal_assignments(fn)
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg != "json":
                    continue
                keys = _dict_literal_keys(kw.value)
                if keys is None and isinstance(kw.value, ast.Name):
                    keys = dict_vars.get(kw.value.id)
                if keys is None:
                    continue
                unknown.update(key for key in keys if key not in allowed)
    return sorted(unknown)


def _dict_literal_assignments(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, set[str]]:
    assignments: dict[str, set[str]] = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            keys = _dict_literal_keys(node.value)
            if keys is None:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assignments[target.id] = keys
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            keys = _dict_literal_keys(node.value)
            if keys is not None:
                assignments[node.target.id] = keys
    return assignments


def _dict_literal_keys(node: ast.AST | None) -> set[str] | None:
    if not isinstance(node, ast.Dict):
        return None
    keys: set[str] = set()
    for key in node.keys:
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            keys.add(key.value)
    return keys


def _route_tests_with_json_body(tree: ast.AST, declared_by_name: dict[str, Any]) -> list[str]:
    route_tests = {
        name
        for name, case in declared_by_name.items()
        if str(_field(case, "target_route") or "").strip()
    }
    if not route_tests:
        return []
    offenders: set[str] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or fn.name not in route_tests:
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and any(kw.arg == "json" for kw in node.keywords):
                offenders.add(fn.name)
    return sorted(offenders)


def _unknown_test_fixtures(tree: ast.AST, allowed_fixtures: Sequence[str]) -> list[str]:
    allowed = _PYTEST_BUILTIN_FIXTURES | {str(name).strip() for name in allowed_fixtures if str(name).strip()}
    unknown: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not node.name.startswith("test"):
            continue
        parametrize_args = _parametrize_arg_names(node)
        for arg in node.args.args + node.args.kwonlyargs:
            name = arg.arg
            if name in {"self", "cls"} or name in parametrize_args:
                continue
            if name not in allowed:
                unknown.add(name)
    return sorted(unknown)


def _parametrize_arg_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names: set[str] = set()
    for dec in node.decorator_list:
        call = dec if isinstance(dec, ast.Call) else None
        if call is None or _last(call.func) != "parametrize" or not call.args:
            continue
        first = call.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            names.update(part.strip() for part in first.value.split(",") if part.strip())
        elif isinstance(first, (ast.List, ast.Tuple)):
            for item in first.elts:
                if isinstance(item, ast.Constant) and isinstance(item.value, str):
                    names.add(item.value)
    return names


def _success_status_only_route_tests(tree: ast.AST, declared_by_name: dict[str, Any]) -> list[str]:
    weak: list[str] = []
    cases_with_route = {
        name
        for name, case in declared_by_name.items()
        if str(_field(case, "target_route") or "").strip()
    }
    if not cases_with_route:
        return []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name not in cases_with_route:
            continue
        asserts = [n for n in ast.walk(node) if isinstance(n, ast.Assert)]
        if not asserts:
            continue
        if all(_is_success_status_code_assert(n.test) for n in asserts):
            weak.append(node.name)
    return sorted(weak)


def _is_success_status_code_assert(node: ast.AST) -> bool:
    if not isinstance(node, ast.Compare) or len(node.ops) != 1 or len(node.comparators) != 1:
        return False
    if not _expr_uses_status_code(node.left):
        return False
    expected = _int_constant(node.comparators[0])
    if expected is None or expected < 200 or expected >= 300:
        return False
    return isinstance(node.ops[0], (ast.Eq, ast.In))


def _expr_uses_status_code(node: ast.AST) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "status_code"


def _int_constant(node: ast.AST) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    return None


def _attr_chain(node) -> str:
    # a.b.c -> "a.b.c"；不是纯属性/名字链返回 ""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


def _last(node) -> str:
    return _attr_chain(node).rsplit(".", 1)[-1]


def _has_skip_or_xfail(tree) -> bool:
    for n in ast.walk(tree):
        # 调用：pytest.skip(...) / skip(...) / pytest.xfail(...)
        if isinstance(n, ast.Call) and _last(n.func) in _SKIP_NAMES:
            return True
        # 装饰器：@pytest.mark.skip / skipif / xfail（带不带参数都算）
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in n.decorator_list:
                target = dec.func if isinstance(dec, ast.Call) else dec
                if _last(target) in _SKIP_NAMES:
                    return True
    return False


def _is_truthy_const(node) -> bool:
    return isinstance(node, ast.Constant) and bool(node.value)


def _has_trivial_assert(tree) -> bool:
    for n in ast.walk(tree):
        if not isinstance(n, ast.Assert):
            continue
        t = n.test
        if _is_truthy_const(t):  # assert True / assert 1 / assert "x"
            return True
        # assert X or True / assert X or 1：短路成恒真
        if isinstance(t, ast.BoolOp) and isinstance(t.op, ast.Or):
            if any(_is_truthy_const(v) for v in t.values):
                return True
        if _is_not_none_check(t):
            return True
    return False


def _is_not_none_check(node) -> bool:
    return (
        isinstance(node, ast.Compare)
        and len(node.ops) == 1
        and isinstance(node.ops[0], ast.IsNot)
        and len(node.comparators) == 1
        and isinstance(node.comparators[0], ast.Constant)
        and node.comparators[0].value is None
    )


def _has_broad_except(tree) -> bool:
    for n in ast.walk(tree):
        if isinstance(n, ast.ExceptHandler):
            if n.type is None:  # bare except:
                return True
            if _last(n.type) in _BROAD_EXC:  # except Exception/BaseException
                return True
    return False


def _count_asserts(tree) -> int:
    return sum(isinstance(n, ast.Assert) for n in ast.walk(tree))


def _count_checks(tree) -> int:
    # 断言式检查：assert 语句 + pytest.raises/warns + assertXxx(...) 调用
    n = _count_asserts(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _last(node.func)
            if name in ("raises", "warns") or name.startswith("assert"):
                n += 1
    return n


def _count_tests(tree) -> int:
    # 与 pytest 默认收集规则一致：test 开头的函数/方法
    return sum(
        isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test")
        for n in ast.walk(tree)
    )
