# 静态契约闸门单测：直接喂首轮/修复版源码对，验证各类「偷偷削弱」都被抓住。
# 这些不起子进程，快；端到端的「模型撒谎」集成验证在 test_e2e_mockllm.py。
from __future__ import annotations

from app.agents.contract_guard import check_generation_contract, check_reflection_contract

# 首轮：两个有意义的断言
OLD = "def test_x():\n    assert f(2) == 5\n    assert g() is True\n"


def test_legit_fix_passes():
    # 只改期望值（5->4），断言形态不变 → 放行
    new = "def test_x():\n    assert f(2) == 4\n    assert g() is True\n"
    assert check_reflection_contract(OLD, new) == []


def test_trivial_assert_true():
    new = "def test_x():\n    assert True\n    assert g() is True\n"
    assert any("空洞断言" in v for v in check_reflection_contract(OLD, new))


def test_assert_or_true_short_circuit():
    new = "def test_x():\n    assert f(2) == 4 or True\n    assert g() is True\n"
    assert any("空洞断言" in v for v in check_reflection_contract(OLD, new))


def test_assert_is_not_none_is_trivial():
    new = "def test_x():\n    value = f(2)\n    assert value is not None\n    assert g() is True\n"
    assert any("空洞断言" in v for v in check_reflection_contract(OLD, new))


def test_pytest_skip_call():
    new = "import pytest\ndef test_x():\n    pytest.skip('todo')\n    assert f(2) == 4\n    assert g() is True\n"
    assert any("skip" in v for v in check_reflection_contract(OLD, new))


def test_skip_marker():
    new = "import pytest\n@pytest.mark.skip\ndef test_x():\n    assert f(2) == 4\n    assert g() is True\n"
    assert any("skip" in v for v in check_reflection_contract(OLD, new))


def test_broad_except_swallow():
    new = (
        "def test_x():\n"
        "    try:\n"
        "        assert f(2) == 4\n"
        "    except Exception:\n"
        "        pass\n"
        "    assert g() is True\n"
    )
    assert any("except" in v for v in check_reflection_contract(OLD, new))


def test_assert_count_drop():
    new = "def test_x():\n    assert f(2) == 4\n"  # 删掉第二个断言
    assert any("断言数量" in v for v in check_reflection_contract(OLD, new))


def test_test_function_count_drop():
    old2 = "def test_a():\n    assert 1 == 2\n\ndef test_b():\n    assert 2 == 3\n"
    new = "def test_a():\n    assert 1 == 2\n"  # 删了一个测试函数
    assert any("测试函数数量" in v for v in check_reflection_contract(old2, new))


def test_unparsable_fix_is_violation():
    assert check_reflection_contract(OLD, "def test_x(:\n    assert 1") != []


def test_empty_test_after_unparsable_old():
    # Codex 第 2 轮的洞：首轮语法错（collection error）→ 反思改成空测试骗绿，必须拦下
    old = "def test_x(:\n    assert f() == 1\n"  # 语法错，old_tree=None
    new = "def test_x():\n    pass\n"
    v = check_reflection_contract(old, new)
    assert any("空测试" in x for x in v)


def test_pass_body_is_violation_even_with_good_old():
    new = "def test_x():\n    pass\n"  # 0 断言
    assert any("空测试" in x for x in check_reflection_contract(OLD, new))


def test_pytest_raises_only_passes_floor():
    # 只用 pytest.raises、没有 assert，仍是有意义的检查 → 放行（首轮无法解析时也不误杀）
    old = "def test_x(:\n    bad\n"
    new = "import pytest\ndef test_x():\n    with pytest.raises(ValueError):\n        f(-1)\n"
    assert check_reflection_contract(old, new) == []


def test_generation_contract_rejects_trivial_assert_and_missing_target_binding():
    content = "def test_x():\n    assert True\n"
    violations = check_generation_contract(
        content,
        [{"test_name": "test_x", "assertion_summary": "空断言"}],
        target_type="function",
        target_ref="target_fn",
    )

    assert any("空洞断言" in violation for violation in violations)
    assert any("target_function/target_route" in violation for violation in violations)


def test_generation_contract_rejects_metadata_drift():
    content = "def test_real():\n    assert target_fn(1) == 2\n"
    violations = check_generation_contract(
        content,
        [{"test_name": "test_declared", "target_function": "target_fn", "assertion_summary": "验证返回值"}],
        target_type="function",
        target_ref="target_fn",
    )

    assert any("缺少 cases 声明" in violation for violation in violations)
    assert any("未对应真实测试函数" in violation for violation in violations)


def test_generation_contract_accepts_target_bound_assertion():
    content = "def test_target():\n    assert target_fn(1) == 2\n"
    violations = check_generation_contract(
        content,
        [{"test_name": "test_target", "target_function": "target_fn", "assertion_summary": "验证 target_fn 返回值"}],
        target_type="function",
        target_ref="target_fn",
    )

    assert violations == []


def test_generation_contract_rejects_unknown_json_request_field():
    content = (
        "def test_create(client):\n"
        "    response = client.post('/prices', json={'sku': 'A', 'made_up': 1})\n"
        "    assert response.status_code == 200\n"
    )
    violations = check_generation_contract(
        content,
        [{"test_name": "test_create", "target_route": "/prices", "assertion_summary": "验证创建价格"}],
        target_type="route",
        target_ref="/prices",
        allowed_request_fields=["sku", "quantity"],
    )

    assert any("made_up" in violation for violation in violations)


def test_generation_contract_rejects_unknown_json_request_field_from_payload_variable():
    content = (
        "def test_create(client):\n"
        "    payload = {'sku': 'A', 'ghost': 1}\n"
        "    response = client.post('/prices', json=payload)\n"
        "    assert response.status_code == 200\n"
    )
    violations = check_generation_contract(
        content,
        [{"test_name": "test_create", "target_route": "/prices", "assertion_summary": "验证创建价格"}],
        target_type="route",
        target_ref="/prices",
        allowed_request_fields=["sku", "quantity"],
    )

    assert any("ghost" in violation for violation in violations)


def test_generation_contract_accepts_json_request_fields_from_model_schema():
    content = (
        "def test_create(client):\n"
        "    response = client.post('/prices', json={'sku': 'A', 'quantity': 1})\n"
        "    assert response.status_code == 200\n"
        "    assert response.json()['sku'] == 'A'\n"
    )
    violations = check_generation_contract(
        content,
        [{"test_name": "test_create", "target_route": "/prices", "assertion_summary": "验证创建价格"}],
        target_type="route",
        target_ref="/prices",
        allowed_request_fields=["sku", "quantity"],
    )

    assert violations == []


def test_generation_contract_rejects_json_body_without_model_schema():
    content = (
        "def test_create(client):\n"
        "    response = client.post('/prices', json={'sku': 'A'})\n"
        "    assert response.status_code == 200\n"
        "    assert response.json()['sku'] == 'A'\n"
    )
    violations = check_generation_contract(
        content,
        [{"test_name": "test_create", "target_route": "/prices", "assertion_summary": "验证创建价格"}],
        target_type="route",
        target_ref="/prices",
        allowed_request_fields=[],
        allowed_fixtures=["client"],
    )

    assert any("请求 JSON 缺少模型字段证据" in violation for violation in violations)


def test_generation_contract_allows_route_without_json_body_when_model_schema_missing():
    content = (
        "def test_health(client):\n"
        "    response = client.get('/health')\n"
        "    assert response.status_code == 200\n"
        "    assert response.json()['ok'] is True\n"
    )
    violations = check_generation_contract(
        content,
        [{"test_name": "test_health", "target_route": "/health", "assertion_summary": "验证健康检查"}],
        target_type="route",
        target_ref="/health",
        allowed_request_fields=[],
        allowed_fixtures=["client"],
    )

    assert violations == []


def test_generation_contract_rejects_unknown_fixture_argument():
    content = "def test_create(client):\n    assert client.get('/health').status_code == 200\n"
    violations = check_generation_contract(
        content,
        [{"test_name": "test_create", "target_route": "/health", "assertion_summary": "验证健康检查"}],
        target_type="route",
        target_ref="/health",
        allowed_fixtures=[],
    )

    assert any("client" in violation for violation in violations)


def test_generation_contract_accepts_project_fixture_argument():
    content = (
        "def test_create(client):\n"
        "    response = client.get('/health')\n"
        "    assert response.status_code == 200\n"
        "    assert response.json()['ok'] is True\n"
    )
    violations = check_generation_contract(
        content,
        [{"test_name": "test_create", "target_route": "/health", "assertion_summary": "验证健康检查"}],
        target_type="route",
        target_ref="/health",
        allowed_fixtures=["client"],
    )

    assert violations == []


def test_generation_contract_rejects_success_status_only_route_oracle():
    content = "def test_health(client):\n    assert client.get('/health').status_code == 200\n"
    violations = check_generation_contract(
        content,
        [{"test_name": "test_health", "target_route": "/health", "assertion_summary": "验证健康检查"}],
        target_type="route",
        target_ref="/health",
        allowed_fixtures=["client"],
    )

    assert any("业务 oracle" in violation for violation in violations)


def test_generation_contract_accepts_route_body_oracle():
    content = (
        "def test_health(client):\n"
        "    response = client.get('/health')\n"
        "    assert response.status_code == 200\n"
        "    assert response.json()['ok'] is True\n"
    )
    violations = check_generation_contract(
        content,
        [{"test_name": "test_health", "target_route": "/health", "assertion_summary": "验证健康检查"}],
        target_type="route",
        target_ref="/health",
        allowed_fixtures=["client"],
    )

    assert violations == []


def test_generation_contract_accepts_route_target_with_method_prefix():
    content = (
        "def test_price(client):\n"
        "    response = client.get('/price/apple')\n"
        "    assert response.status_code == 200\n"
        "    assert response.json()['item'] == 'apple'\n"
    )
    violations = check_generation_contract(
        content,
        [{"test_name": "test_price", "target_route": "/price/{item}", "assertion_summary": "验证价格查询"}],
        target_type="route",
        target_ref="GET /price/{item}",
        allowed_fixtures=["client"],
    )

    assert violations == []


def test_generation_contract_allows_error_status_route_check():
    content = "def test_missing(client):\n    assert client.get('/missing').status_code == 404\n"
    violations = check_generation_contract(
        content,
        [{"test_name": "test_missing", "target_route": "/missing", "assertion_summary": "验证缺失资源"}],
        target_type="route",
        target_ref="/missing",
        allowed_fixtures=["client"],
    )

    assert violations == []


def test_generation_contract_accepts_builtin_and_parametrize_arguments():
    content = (
        "import pytest\n"
        "@pytest.mark.parametrize('value,expected', [(1, 2)])\n"
        "def test_calc(tmp_path, value, expected):\n"
        "    assert value + 1 == expected\n"
    )
    violations = check_generation_contract(
        content,
        [{"test_name": "test_calc", "target_function": "calc", "assertion_summary": "验证参数化计算"}],
        target_type="function",
        target_ref="calc",
        allowed_fixtures=[],
    )

    assert violations == []


def test_generation_contract_accepts_parametrized_case_metadata_names():
    content = (
        "import pytest\n"
        "from shop.pricing import apply_discount\n"
        "@pytest.mark.parametrize('total,is_member,expected', [(100, True, 90.0), (50, False, 50.0)])\n"
        "def test_apply_discount(total, is_member, expected):\n"
        "    assert apply_discount(total, is_member) == expected\n"
    )
    violations = check_generation_contract(
        content,
        [
            {
                "test_name": "test_apply_discount[100-True-90.0]",
                "target_function": "apply_discount",
                "assertion_summary": "会员满 100 打折",
            },
            {
                "test_name": "test_apply_discount[50-False-50.0]",
                "target_function": "apply_discount",
                "assertion_summary": "非会员不打折",
            },
        ],
        target_type="function",
        target_ref="apply_discount",
        allowed_fixtures=[],
    )

    assert violations == []
