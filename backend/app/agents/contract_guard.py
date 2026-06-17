# Reflection 契约的静态闸门：别让被审的人自己打分。
# 不信任模型自报的 lowered_assertion_strength，直接比对首轮/修复版 AST，
# 抓 pytest.skip/xfail、空洞断言、宽泛 except 吞异常、断言/测试数缩水。
from __future__ import annotations

import ast
from typing import Any, Sequence

_SKIP_NAMES = {"skip", "skipif", "xfail"}
_BROAD_EXC = {"Exception", "BaseException"}
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
    return str(name).split("::")[-1].split("[", 1)[0].strip()


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
    parts = value.strip().split(maxsplit=1)
    if len(parts) == 2 and parts[0].upper() in {"GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"}:
        return parts[1].strip()
    return value.strip()


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
