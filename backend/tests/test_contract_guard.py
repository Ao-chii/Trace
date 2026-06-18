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


def test_generation_contract_accepts_route_target_with_type_and_method_prefix():
    content = (
        "def test_price(client):\n"
        "    response = client.get('/price/apple')\n"
        "    assert response.status_code == 200\n"
        "    assert response.json()['item'] == 'apple'\n"
    )
    violations = check_generation_contract(
        content,
        [{"test_name": "test_price", "target_route": "route:GET /price/{item}", "assertion_summary": "验证价格查询"}],
        target_type="route",
        target_ref="GET /price/{item}",
        allowed_fixtures=["client"],
    )

    assert violations == []


PRICE_ROUTE_SOURCE_CONTEXT = """## shop/api.py:11-15 (get_price)
```python
@app.get("/price/{item}")
def get_price(item: str, member: bool = False):
    if item not in _PRICES:
        raise HTTPException(status_code=404, detail="item not found")
    return {"item": item, "total": apply_discount(_PRICES[item], member)}
```"""


def test_generation_contract_rejects_empty_path_param_route_case():
    content = (
        "from fastapi.testclient import TestClient\n"
        "from shop.api import app\n\n"
        "def test_get_price_empty_string_item(monkeypatch):\n"
        "    monkeypatch.setattr('shop.api._PRICES', {'': 10})\n"
        "    client = TestClient(app)\n"
        "    response = client.get('/price/')\n"
        "    assert response.status_code == 200\n"
        "    assert response.json()['item'] == ''\n"
    )
    violations = check_generation_contract(
        content,
        [
            {
                "test_name": "test_get_price_empty_string_item",
                "target_route": "/price/{item}",
                "assertion_summary": "空字符串 item 可查询",
            }
        ],
        target_type="route",
        target_ref="GET /price/{item}",
        allowed_fixtures=["monkeypatch"],
        source_context=PRICE_ROUTE_SOURCE_CONTEXT,
    )

    assert any("空 path 参数" in violation for violation in violations)


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


def test_generation_contract_accepts_class_method_case_metadata_names():
    content = (
        "from shop.pricing import apply_discount\n\n"
        "class TestApplyDiscount:\n"
        "    def test_member_100(self):\n"
        "        assert apply_discount(100, True) == 90.0\n"
    )
    violations = check_generation_contract(
        content,
        [
            {
                "test_name": "TestApplyDiscount.test_member_100",
                "target_function": "apply_discount",
                "assertion_summary": "会员满 100 打折",
            }
        ],
        target_type="function",
        target_ref="apply_discount",
        allowed_fixtures=[],
    )

    assert violations == []


APPLY_DISCOUNT_SOURCE_CONTEXT = """## shop/pricing.py:5-9 (apply_discount)
```python
def apply_discount(total, is_member):
    if is_member and total >= 100:
        return round(total * 0.9, 2)
    return round(total, 2)
```"""

PRICE_ROUTE_WITH_PRICING_SOURCE_CONTEXT = PRICE_ROUTE_SOURCE_CONTEXT + "\n" + APPLY_DISCOUNT_SOURCE_CONTEXT


def test_generation_contract_rejects_boundary_oracle_that_contradicts_source_context():
    content = (
        "from shop.pricing import apply_discount\n\n"
        "def test_apply_discount_negative_total():\n"
        "    result = apply_discount(-50, True)\n"
        "    assert result == round(-50 * 0.9, 2)\n"
    )
    violations = check_generation_contract(
        content,
        [
            {
                "test_name": "test_apply_discount_negative_total",
                "target_function": "apply_discount",
                "assertion_summary": "负数边界仍按会员折扣",
            }
        ],
        target_type="function",
        target_ref="apply_discount",
        source_context=APPLY_DISCOUNT_SOURCE_CONTEXT,
    )

    assert any("源码行为" in violation for violation in violations)


def test_generation_contract_accepts_boundary_oracle_matching_source_context():
    content = (
        "from shop.pricing import apply_discount\n\n"
        "def test_apply_discount_negative_total():\n"
        "    result = apply_discount(-50, True)\n"
        "    assert result == -50.0\n"
    )
    violations = check_generation_contract(
        content,
        [
            {
                "test_name": "test_apply_discount_negative_total",
                "target_function": "apply_discount",
                "assertion_summary": "负数边界按源码 fallback 返回原值",
            }
        ],
        target_type="function",
        target_ref="apply_discount",
        source_context=APPLY_DISCOUNT_SOURCE_CONTEXT,
    )

    assert violations == []


def test_generation_contract_rejects_route_oracle_that_contradicts_handler_source():
    content = (
        "import pytest\n"
        "from fastapi.testclient import TestClient\n"
        "from shop.api import app\n\n"
        "client = TestClient(app)\n\n"
        "@pytest.mark.parametrize('item, member, expected_total', [\n"
        "    ('apple', True, 4.5),\n"
        "    ('banana', True, 27.0),\n"
        "])\n"
        "def test_get_price_existing_item(item, member, expected_total, monkeypatch):\n"
        "    monkeypatch.setattr('shop.api._PRICES', {'apple': 5.0, 'banana': 30.0})\n"
        "    response = client.get(f'/price/{item}', params={'member': member})\n"
        "    data = response.json()\n"
        "    assert response.status_code == 200\n"
        "    assert data['item'] == item\n"
        "    assert data['total'] == expected_total\n"
    )
    violations = check_generation_contract(
        content,
        [
            {
                "test_name": "test_get_price_existing_item[apple-True-4.5]",
                "target_route": "/price/{item}",
                "assertion_summary": "会员商品价格",
            }
        ],
        target_type="route",
        target_ref="GET /price/{item}",
        allowed_fixtures=["monkeypatch"],
        source_context=PRICE_ROUTE_WITH_PRICING_SOURCE_CONTEXT,
    )

    assert any("路由响应 oracle" in violation for violation in violations)


def test_generation_contract_accepts_route_oracle_matching_handler_source():
    content = (
        "import pytest\n"
        "from fastapi.testclient import TestClient\n"
        "from shop.api import app\n\n"
        "client = TestClient(app)\n\n"
        "@pytest.mark.parametrize('item, member, expected_total', [\n"
        "    ('apple', True, 5.0),\n"
        "    ('banana', True, 30.0),\n"
        "])\n"
        "def test_get_price_existing_item(item, member, expected_total, monkeypatch):\n"
        "    monkeypatch.setattr('shop.api._PRICES', {'apple': 5.0, 'banana': 30.0})\n"
        "    response = client.get(f'/price/{item}', params={'member': member})\n"
        "    data = response.json()\n"
        "    assert response.status_code == 200\n"
        "    assert data['item'] == item\n"
        "    assert data['total'] == expected_total\n"
    )
    violations = check_generation_contract(
        content,
        [
            {
                "test_name": "test_get_price_existing_item[apple-True-5.0]",
                "target_route": "/price/{item}",
                "assertion_summary": "会员商品价格按源码阈值计算",
            }
        ],
        target_type="route",
        target_ref="GET /price/{item}",
        allowed_fixtures=["monkeypatch"],
        source_context=PRICE_ROUTE_WITH_PRICING_SOURCE_CONTEXT,
    )

    assert violations == []


def test_generation_contract_accepts_route_oracle_with_query_string_matching_source():
    content = (
        "from fastapi.testclient import TestClient\n"
        "from shop.api import app\n"
        "from shop.pricing import apply_discount\n\n"
        "def test_get_price_item_exists(monkeypatch):\n"
        "    prices = {'widget': 250.0}\n"
        "    monkeypatch.setattr('shop.api._PRICES', prices)\n"
        "    client = TestClient(app)\n"
        "    resp = client.get('/price/widget')\n"
        "    assert resp.status_code == 200\n"
        "    data = resp.json()\n"
        "    assert data == {'item': 'widget', 'total': apply_discount(prices['widget'], False)}\n"
        "    resp = client.get('/price/widget?member=true')\n"
        "    assert resp.status_code == 200\n"
        "    data = resp.json()\n"
        "    assert data == {'item': 'widget', 'total': apply_discount(prices['widget'], True)}\n"
    )
    violations = check_generation_contract(
        content,
        [
            {
                "test_name": "test_get_price_item_exists",
                "target_route": "/price/{item}",
                "assertion_summary": "路由价格按 handler 和 apply_discount 计算",
            }
        ],
        target_type="route",
        target_ref="GET /price/{item}",
        allowed_fixtures=["monkeypatch"],
        source_context=PRICE_ROUTE_WITH_PRICING_SOURCE_CONTEXT,
    )

    assert violations == []


def test_generation_contract_rejects_route_error_oracle_that_contradicts_handler_source():
    content = (
        "from fastapi.testclient import TestClient\n"
        "from shop.api import app\n\n"
        "def test_get_price_missing_item(monkeypatch):\n"
        "    monkeypatch.setattr('shop.api._PRICES', {})\n"
        "    client = TestClient(app)\n"
        "    response = client.get('/price/ghost')\n"
        "    assert response.status_code == 500\n"
        "    assert response.json()['detail'] == 'item not found'\n"
    )
    violations = check_generation_contract(
        content,
        [
            {
                "test_name": "test_get_price_missing_item",
                "target_route": "/price/{item}",
                "assertion_summary": "缺失商品返回错误",
            }
        ],
        target_type="route",
        target_ref="GET /price/{item}",
        allowed_fixtures=["monkeypatch"],
        source_context=PRICE_ROUTE_WITH_PRICING_SOURCE_CONTEXT,
    )

    assert any("路由响应 oracle" in violation for violation in violations)


def test_generation_contract_accepts_route_error_oracle_matching_handler_source():
    content = (
        "from fastapi.testclient import TestClient\n"
        "from shop.api import app\n\n"
        "def test_get_price_missing_item(monkeypatch):\n"
        "    monkeypatch.setattr('shop.api._PRICES', {})\n"
        "    client = TestClient(app)\n"
        "    response = client.get('/price/ghost')\n"
        "    assert response.status_code == 404\n"
        "    assert response.json()['detail'] == 'item not found'\n"
    )
    violations = check_generation_contract(
        content,
        [
            {
                "test_name": "test_get_price_missing_item",
                "target_route": "/price/{item}",
                "assertion_summary": "缺失商品按源码返回 404",
            }
        ],
        target_type="route",
        target_ref="GET /price/{item}",
        allowed_fixtures=["monkeypatch"],
        source_context=PRICE_ROUTE_WITH_PRICING_SOURCE_CONTEXT,
    )

    assert violations == []


def test_generation_contract_rejects_boundary_raises_without_source_raise():
    content = (
        "import pytest\n"
        "from shop.pricing import apply_discount\n\n"
        "def test_apply_discount_negative_total():\n"
        "    with pytest.raises(ValueError):\n"
        "        apply_discount(-50, True)\n"
    )
    violations = check_generation_contract(
        content,
        [
            {
                "test_name": "test_apply_discount_negative_total",
                "target_function": "apply_discount",
                "assertion_summary": "负数边界应抛异常",
            }
        ],
        target_type="function",
        target_ref="apply_discount",
        source_context=APPLY_DISCOUNT_SOURCE_CONTEXT,
    )

    assert any("没有可见 raise 证据" in violation for violation in violations)


VALIDATE_QUANTITY_SOURCE_CONTEXT = """## shop/pricing.py:11-14 (validate_quantity)
```python
def validate_quantity(quantity):
    if quantity <= 0:
        raise ValueError(f"quantity must be positive: {quantity}")
    return quantity
```"""


def test_generation_contract_rejects_exception_type_oracle_that_contradicts_source_context():
    content = (
        "import pytest\n"
        "from shop.pricing import validate_quantity\n\n"
        "def test_validate_quantity_zero():\n"
        "    with pytest.raises(TypeError):\n"
        "        validate_quantity(0)\n"
    )
    violations = check_generation_contract(
        content,
        [
            {
                "test_name": "test_validate_quantity_zero",
                "target_function": "validate_quantity",
                "assertion_summary": "数量为 0 时抛出类型错误",
            }
        ],
        target_type="function",
        target_ref="validate_quantity",
        source_context=VALIDATE_QUANTITY_SOURCE_CONTEXT,
    )

    assert any("异常类型" in violation for violation in violations)


def test_generation_contract_rejects_exception_match_oracle_that_contradicts_source_context():
    content = (
        "import pytest\n"
        "from shop.pricing import validate_quantity\n\n"
        "def test_validate_quantity_zero_message():\n"
        "    with pytest.raises(ValueError, match='negative amount'):\n"
        "        validate_quantity(0)\n"
    )
    violations = check_generation_contract(
        content,
        [
            {
                "test_name": "test_validate_quantity_zero_message",
                "target_function": "validate_quantity",
                "assertion_summary": "数量为 0 时说明是负数金额",
            }
        ],
        target_type="function",
        target_ref="validate_quantity",
        source_context=VALIDATE_QUANTITY_SOURCE_CONTEXT,
    )

    assert any("异常消息" in violation for violation in violations)


def test_generation_contract_accepts_exception_oracle_matching_source_context():
    content = (
        "import pytest\n"
        "from shop.pricing import validate_quantity\n\n"
        "def test_validate_quantity_zero_message():\n"
        "    with pytest.raises(ValueError, match='quantity must be positive: 0'):\n"
        "        validate_quantity(0)\n"
    )
    violations = check_generation_contract(
        content,
        [
            {
                "test_name": "test_validate_quantity_zero_message",
                "target_function": "validate_quantity",
                "assertion_summary": "数量为 0 时按源码抛出 ValueError",
            }
        ],
        target_type="function",
        target_ref="validate_quantity",
        source_context=VALIDATE_QUANTITY_SOURCE_CONTEXT,
    )

    assert violations == []


CUSTOM_EXCEPTION_SOURCE_CONTEXT = """## shop/pricing.py:11-22 (custom exceptions)
```python
class QuantityError(ValueError):
    pass

def validate_custom_quantity(quantity):
    if quantity <= 0:
        raise QuantityError(f"bad quantity: {quantity}")
    return quantity

def fallback_custom_quantity(quantity):
    try:
        validate_custom_quantity(quantity)
    except ValueError:
        return 1
    return quantity
```"""


def test_generation_contract_accepts_custom_exception_subclass_oracle_matching_source_context():
    content = (
        "import pytest\n"
        "from shop.pricing import validate_custom_quantity\n\n"
        "def test_validate_custom_quantity_zero():\n"
        "    with pytest.raises(ValueError, match='bad quantity: 0'):\n"
        "        validate_custom_quantity(0)\n"
    )
    violations = check_generation_contract(
        content,
        [
            {
                "test_name": "test_validate_custom_quantity_zero",
                "target_function": "validate_custom_quantity",
                "assertion_summary": "自定义异常继承 ValueError",
            }
        ],
        target_type="function",
        target_ref="validate_custom_quantity",
        source_context=CUSTOM_EXCEPTION_SOURCE_CONTEXT,
    )

    assert violations == []


def test_generation_contract_rejects_custom_exception_wrong_parent_oracle():
    content = (
        "import pytest\n"
        "from shop.pricing import validate_custom_quantity\n\n"
        "def test_validate_custom_quantity_zero():\n"
        "    with pytest.raises(TypeError):\n"
        "        validate_custom_quantity(0)\n"
    )
    violations = check_generation_contract(
        content,
        [
            {
                "test_name": "test_validate_custom_quantity_zero",
                "target_function": "validate_custom_quantity",
                "assertion_summary": "自定义异常被误判为 TypeError",
            }
        ],
        target_type="function",
        target_ref="validate_custom_quantity",
        source_context=CUSTOM_EXCEPTION_SOURCE_CONTEXT,
    )

    assert any("异常类型" in violation for violation in violations)


def test_generation_contract_rejects_custom_exception_handler_return_oracle_that_contradicts_source_context():
    content = (
        "from shop.pricing import fallback_custom_quantity\n\n"
        "def test_fallback_custom_quantity_zero():\n"
        "    assert fallback_custom_quantity(0) == 0\n"
    )
    violations = check_generation_contract(
        content,
        [
            {
                "test_name": "test_fallback_custom_quantity_zero",
                "target_function": "fallback_custom_quantity",
                "assertion_summary": "自定义异常没有被父类 except 捕获",
            }
        ],
        target_type="function",
        target_ref="fallback_custom_quantity",
        source_context=CUSTOM_EXCEPTION_SOURCE_CONTEXT,
    )

    assert any("源码行为" in violation for violation in violations)


TRY_VALIDATE_QUANTITY_SOURCE_CONTEXT = VALIDATE_QUANTITY_SOURCE_CONTEXT + """
## shop/pricing.py:16-25 (normalize_quantity)
```python
def normalize_quantity(quantity):
    try:
        validate_quantity(quantity)
    except ValueError:
        raise ValueError(f"invalid quantity: {quantity}")
    return quantity

def fallback_quantity(quantity):
    try:
        validate_quantity(quantity)
    except ValueError:
        return 1
    return quantity
```"""


def test_generation_contract_rejects_try_except_exception_oracle_that_contradicts_source_context():
    content = (
        "import pytest\n"
        "from shop.pricing import normalize_quantity\n\n"
        "def test_normalize_quantity_zero_message():\n"
        "    with pytest.raises(ValueError, match='quantity must be positive'):\n"
        "        normalize_quantity(0)\n"
    )
    violations = check_generation_contract(
        content,
        [
            {
                "test_name": "test_normalize_quantity_zero_message",
                "target_function": "normalize_quantity",
                "assertion_summary": "数量为 0 时保留底层错误消息",
            }
        ],
        target_type="function",
        target_ref="normalize_quantity",
        source_context=TRY_VALIDATE_QUANTITY_SOURCE_CONTEXT,
    )

    assert any("异常消息" in violation for violation in violations)


def test_generation_contract_rejects_try_except_return_oracle_that_contradicts_source_context():
    content = (
        "from shop.pricing import fallback_quantity\n\n"
        "def test_fallback_quantity_zero():\n"
        "    assert fallback_quantity(0) == 0\n"
    )
    violations = check_generation_contract(
        content,
        [
            {
                "test_name": "test_fallback_quantity_zero",
                "target_function": "fallback_quantity",
                "assertion_summary": "数量为 0 时返回原值",
            }
        ],
        target_type="function",
        target_ref="fallback_quantity",
        source_context=TRY_VALIDATE_QUANTITY_SOURCE_CONTEXT,
    )

    assert any("源码行为" in violation for violation in violations)


def test_generation_contract_accepts_try_except_exception_oracle_matching_source_context():
    content = (
        "import pytest\n"
        "from shop.pricing import normalize_quantity\n\n"
        "def test_normalize_quantity_zero_message():\n"
        "    with pytest.raises(ValueError, match='invalid quantity: 0'):\n"
        "        normalize_quantity(0)\n"
    )
    violations = check_generation_contract(
        content,
        [
            {
                "test_name": "test_normalize_quantity_zero_message",
                "target_function": "normalize_quantity",
                "assertion_summary": "数量为 0 时按源码转换错误消息",
            }
        ],
        target_type="function",
        target_ref="normalize_quantity",
        source_context=TRY_VALIDATE_QUANTITY_SOURCE_CONTEXT,
    )

    assert violations == []


def test_generation_contract_accepts_try_except_return_oracle_matching_source_context():
    content = (
        "from shop.pricing import fallback_quantity\n\n"
        "def test_fallback_quantity_zero():\n"
        "    assert fallback_quantity(0) == 1\n"
    )
    violations = check_generation_contract(
        content,
        [
            {
                "test_name": "test_fallback_quantity_zero",
                "target_function": "fallback_quantity",
                "assertion_summary": "数量为 0 时按源码 fallback 到 1",
            }
        ],
        target_type="function",
        target_ref="fallback_quantity",
        source_context=TRY_VALIDATE_QUANTITY_SOURCE_CONTEXT,
    )

    assert violations == []


FINALLY_VALIDATE_QUANTITY_SOURCE_CONTEXT = VALIDATE_QUANTITY_SOURCE_CONTEXT + """
## shop/pricing.py:27-38 (finally_quantity)
```python
def cleanup_quantity(quantity):
    try:
        validate_quantity(quantity)
    finally:
        marker = "cleaned"
    return quantity

def forced_quantity(quantity):
    try:
        validate_quantity(quantity)
    finally:
        return 99
```"""


def test_generation_contract_accepts_finally_cleanup_exception_oracle_matching_source_context():
    content = (
        "import pytest\n"
        "from shop.pricing import cleanup_quantity\n\n"
        "def test_cleanup_quantity_zero_message():\n"
        "    with pytest.raises(ValueError, match='quantity must be positive: 0'):\n"
        "        cleanup_quantity(0)\n"
    )
    violations = check_generation_contract(
        content,
        [
            {
                "test_name": "test_cleanup_quantity_zero_message",
                "target_function": "cleanup_quantity",
                "assertion_summary": "finally 清理不改变源码异常",
            }
        ],
        target_type="function",
        target_ref="cleanup_quantity",
        source_context=FINALLY_VALIDATE_QUANTITY_SOURCE_CONTEXT,
    )

    assert violations == []


def test_generation_contract_rejects_finally_return_suppressed_exception_oracle():
    content = (
        "import pytest\n"
        "from shop.pricing import forced_quantity\n\n"
        "def test_forced_quantity_zero_raises():\n"
        "    with pytest.raises(ValueError):\n"
        "        forced_quantity(0)\n"
    )
    violations = check_generation_contract(
        content,
        [
            {
                "test_name": "test_forced_quantity_zero_raises",
                "target_function": "forced_quantity",
                "assertion_summary": "数量为 0 时仍抛出底层异常",
            }
        ],
        target_type="function",
        target_ref="forced_quantity",
        source_context=FINALLY_VALIDATE_QUANTITY_SOURCE_CONTEXT,
    )

    assert any("没有可见 raise 证据" in violation for violation in violations)


def test_generation_contract_accepts_finally_return_oracle_matching_source_context():
    content = (
        "from shop.pricing import forced_quantity\n\n"
        "def test_forced_quantity_zero():\n"
        "    assert forced_quantity(0) == 99\n"
    )
    violations = check_generation_contract(
        content,
        [
            {
                "test_name": "test_forced_quantity_zero",
                "target_function": "forced_quantity",
                "assertion_summary": "finally return 覆盖底层异常并返回 99",
            }
        ],
        target_type="function",
        target_ref="forced_quantity",
        source_context=FINALLY_VALIDATE_QUANTITY_SOURCE_CONTEXT,
    )

    assert violations == []


def test_generation_contract_rejects_finally_return_oracle_that_contradicts_source_context():
    content = (
        "from shop.pricing import forced_quantity\n\n"
        "def test_forced_quantity_zero():\n"
        "    assert forced_quantity(0) == 1\n"
    )
    violations = check_generation_contract(
        content,
        [
            {
                "test_name": "test_forced_quantity_zero",
                "target_function": "forced_quantity",
                "assertion_summary": "数量为 0 时 fallback 到 1",
            }
        ],
        target_type="function",
        target_ref="forced_quantity",
        source_context=FINALLY_VALIDATE_QUANTITY_SOURCE_CONTEXT,
    )

    assert any("源码行为" in violation for violation in violations)


def test_generation_contract_rejects_rounding_oracle_that_contradicts_source_context():
    content = (
        "from shop.pricing import apply_discount\n\n"
        "def test_rounding_behavior():\n"
        "    assert apply_discount(1.015, False) == 1.02\n"
    )
    violations = check_generation_contract(
        content,
        [
            {
                "test_name": "test_rounding_behavior",
                "target_function": "apply_discount",
                "assertion_summary": "rounding behavior for float precision",
            }
        ],
        target_type="function",
        target_ref="apply_discount",
        source_context=APPLY_DISCOUNT_SOURCE_CONTEXT,
    )

    assert any("源码行为" in violation for violation in violations)


def test_generation_contract_rejects_parametrized_oracle_that_contradicts_source_context():
    content = (
        "import pytest\n"
        "from shop.pricing import apply_discount\n\n"
        "@pytest.mark.parametrize('total,is_member,expected', [(999.99, True, 899.991)])\n"
        "def test_apply_discount(total, is_member, expected):\n"
        "    result = apply_discount(total, is_member)\n"
        "    assert result == expected\n"
    )
    violations = check_generation_contract(
        content,
        [
            {
                "test_name": "test_apply_discount[999.99-True-899.991]",
                "target_function": "apply_discount",
                "assertion_summary": "会员金额打折",
            }
        ],
        target_type="function",
        target_ref="apply_discount",
        source_context=APPLY_DISCOUNT_SOURCE_CONTEXT,
    )

    assert any("源码行为" in violation for violation in violations)


def test_generation_contract_accepts_parametrized_oracle_matching_source_context():
    content = (
        "import pytest\n"
        "from shop.pricing import apply_discount\n\n"
        "@pytest.mark.parametrize('total,is_member,expected', [(999.99, True, 899.99)])\n"
        "def test_apply_discount(total, is_member, expected):\n"
        "    result = apply_discount(total, is_member)\n"
        "    assert result == expected\n"
    )
    violations = check_generation_contract(
        content,
        [
            {
                "test_name": "test_apply_discount[999.99-True-899.99]",
                "target_function": "apply_discount",
                "assertion_summary": "会员金额按源码 round 后打折",
            }
        ],
        target_type="function",
        target_ref="apply_discount",
        source_context=APPLY_DISCOUNT_SOURCE_CONTEXT,
    )

    assert violations == []


def test_generation_contract_rejects_redefining_target_function_in_test_file():
    content = (
        "def loyalty_points(years):\n"
        "    if years >= 3:\n"
        "        return 100\n"
        "    return 10\n\n"
        "def test_loyalty_points_equal_3():\n"
        "    assert loyalty_points(3) == 100\n"
    )
    violations = check_generation_contract(
        content,
        [
            {
                "test_name": "test_loyalty_points_equal_3",
                "target_function": "loyalty_points",
                "assertion_summary": "满 3 年送 100 分",
            }
        ],
        target_type="function",
        target_ref="loyalty_points",
    )

    assert any("重新定义被测目标" in violation for violation in violations)
