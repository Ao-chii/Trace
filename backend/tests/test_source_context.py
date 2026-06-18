from __future__ import annotations

from pathlib import Path

from app.schemas.tools import (
    AnalyzeProjectOutput,
    ExistingTestInfo,
    FixtureInfo,
    FunctionInfo,
    ModelFieldInfo,
    ModelInfo,
    RouteInfo,
    TestFunctionInfo as ExistingTestFunctionInfo,
)
from app.services.source_context import build_source_context_bundle
from app.tools.analyze import analyze_project
from app.tools.base import ToolContext
from app.schemas.tools import AnalyzeProjectInput


def _ctx(tmp_path: Path) -> ToolContext:
    write_dir = tmp_path / "tests" / "generated"
    write_dir.mkdir(parents=True, exist_ok=True)
    return ToolContext(root=tmp_path, test_write_dir=write_dir)


_SAMPLE = (
    "import math\n"
    "\n"
    "\n"
    "def helper(x):\n"
    "    return x + 1\n"
    "\n"
    "\n"
    "def target_fn(a, b):\n"
    "    # 目标函数体\n"
    "    return a * b + helper(a)\n"
)


def test_slices_target_function_not_whole_file(tmp_path):
    (tmp_path / "calc.py").write_text(_SAMPLE, encoding="utf-8")
    analysis = AnalyzeProjectOutput(
        functions=[FunctionInfo(name="target_fn", signature="target_fn(a, b)", file="calc.py")]
    )

    bundle = build_source_context_bundle(_ctx(tmp_path), ["target_fn"], analysis)

    assert bundle.context_completeness.status == "complete"
    assert len(bundle.snippets) == 1
    snip = bundle.snippets[0]
    # 只切目标函数：起始行落在 def target_fn 上，不是文件第 1 行。
    assert snip.target_ref == "target_fn"
    assert snip.start_line == 8
    assert snip.end_line == 10
    assert "def target_fn" in bundle.source_context_text
    assert "def helper" not in bundle.source_context_text


def test_slices_route_handler_with_decorator(tmp_path):
    src = (
        "from fastapi import APIRouter\n"
        "\n"
        "router = APIRouter()\n"
        "\n"
        "\n"
        "@router.get('/items')\n"
        "def list_items():\n"
        "    return []\n"
    )
    (tmp_path / "api.py").write_text(src, encoding="utf-8")
    analysis = AnalyzeProjectOutput(
        routes=[RouteInfo(method="GET", path="/items", handler="list_items", file="api.py")]
    )

    bundle = build_source_context_bundle(_ctx(tmp_path), ["list_items"], analysis)

    assert bundle.context_completeness.status == "complete"
    # 装饰器要带上，否则看不出这是个路由。
    assert "@router.get('/items')" in bundle.source_context_text
    assert bundle.snippets[0].start_line == 6


def test_route_path_target_resolves_to_handler(tmp_path):
    src = (
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "\n"
        "@router.get('/items')\n"
        "def list_items():\n"
        "    return []\n"
    )
    (tmp_path / "api.py").write_text(src, encoding="utf-8")
    analysis = AnalyzeProjectOutput(
        routes=[RouteInfo(method="GET", path="/items", handler="list_items", file="api.py")]
    )

    bundle = build_source_context_bundle(_ctx(tmp_path), ["/items"], analysis)

    assert bundle.context_completeness.status == "complete"
    assert bundle.snippets[0].target_ref == "list_items"
    assert bundle.snippets[0].retrieval_source == "framework_scanner"
    assert "def list_items" in bundle.source_context_text


def test_missing_symbol_marks_context_incomplete(tmp_path):
    (tmp_path / "calc.py").write_text(_SAMPLE, encoding="utf-8")
    analysis = AnalyzeProjectOutput(
        functions=[FunctionInfo(name="target_fn", signature="target_fn(a, b)", file="calc.py")]
    )

    bundle = build_source_context_bundle(_ctx(tmp_path), ["does_not_exist"], analysis)

    assert bundle.context_completeness.status == "incomplete"
    assert bundle.context_completeness.context_incomplete is True
    assert "does_not_exist" in bundle.context_completeness.missing_targets


def test_analyze_project_symbol_scope_scans_project_then_source_context_resolves(tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "pricing.py").write_text(_SAMPLE, encoding="utf-8")

    analysis = analyze_project(_ctx(tmp_path), AnalyzeProjectInput(target_scope=["target_fn"]))
    bundle = build_source_context_bundle(_ctx(tmp_path), ["target_fn"], analysis)

    assert any(fn.name == "target_fn" and fn.file == "shop/pricing.py" for fn in analysis.functions)
    assert bundle.context_completeness.status == "complete"
    assert bundle.snippets[0].path == "shop/pricing.py"


def test_analyze_project_extracts_pytest_fixtures_from_conftest(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "conftest.py").write_text(
        "import pytest\n"
        "\n"
        "@pytest.fixture\n"
        "def client(app):\n"
        "    return app.test_client()\n",
        encoding="utf-8",
    )

    analysis = analyze_project(_ctx(tmp_path), AnalyzeProjectInput(target_scope=["."]))

    assert len(analysis.fixtures) == 1
    fixture = analysis.fixtures[0]
    assert fixture.name == "client"
    assert fixture.file == "tests/conftest.py"
    assert fixture.dependencies == ["app"]


def test_analyze_project_extracts_pydantic_model_fields(tmp_path):
    (tmp_path / "api.py").write_text(
        "from pydantic import BaseModel, Field\n"
        "\n"
        "class PriceRequest(BaseModel):\n"
        "    sku: str\n"
        "    quantity: int = 1\n"
        "    coupon: str | None = Field(default=None)\n",
        encoding="utf-8",
    )

    analysis = analyze_project(_ctx(tmp_path), AnalyzeProjectInput(target_scope=["."]))

    assert len(analysis.models) == 1
    model = analysis.models[0]
    assert model.name == "PriceRequest"
    fields = {field.name: field for field in model.fields}
    assert fields["sku"].type == "str"
    assert fields["sku"].required is True
    assert fields["quantity"].type == "int"
    assert fields["quantity"].required is False
    assert fields["quantity"].default == "1"
    assert fields["coupon"].type == "str | None"
    assert fields["coupon"].required is False


def test_source_context_includes_pydantic_model_evidence(tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "api.py").write_text(
        "from pydantic import BaseModel\n"
        "\n"
        "class PriceRequest(BaseModel):\n"
        "    sku: str\n"
        "    quantity: int = 1\n"
        "\n"
        "def quote_price(req: PriceRequest):\n"
        "    return req.quantity * 10\n",
        encoding="utf-8",
    )
    analysis = analyze_project(_ctx(tmp_path), AnalyzeProjectInput(target_scope=["quote_price"]))

    bundle = build_source_context_bundle(_ctx(tmp_path), ["quote_price"], analysis)

    assert bundle.context_completeness.status == "complete"
    refs = {snippet.target_ref for snippet in bundle.snippets}
    assert "quote_price" in refs
    assert "model:PriceRequest" in refs
    assert "class PriceRequest" in bundle.source_context_text
    assert "quantity: int = 1" in bundle.source_context_text


def test_source_context_includes_fixture_and_existing_test_evidence(tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "shop" / "pricing.py").write_text(_SAMPLE, encoding="utf-8")
    (tmp_path / "tests" / "conftest.py").write_text(
        "import pytest\n"
        "\n"
        "@pytest.fixture\n"
        "def client():\n"
        "    return object()\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_pricing.py").write_text(
        "from shop.pricing import target_fn\n"
        "\n"
        "def test_existing_pricing_case(client):\n"
        "    assert target_fn(2, 3) == 7\n",
        encoding="utf-8",
    )
    analysis = analyze_project(_ctx(tmp_path), AnalyzeProjectInput(target_scope=["target_fn"]))

    bundle = build_source_context_bundle(_ctx(tmp_path), ["target_fn"], analysis)

    assert bundle.context_completeness.status == "complete"
    refs = {snippet.target_ref for snippet in bundle.snippets}
    assert "target_fn" in refs
    assert "fixture:client" in refs
    assert "existing_test:tests/test_pricing.py::test_existing_pricing_case" in refs
    assert "def client" in bundle.source_context_text
    assert "def test_existing_pricing_case" in bundle.source_context_text


def test_source_context_snippets_carry_structured_retrieval_evidence(tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "shop" / "pricing.py").write_text(
        "from pydantic import BaseModel\n"
        "\n"
        "class PriceRequest(BaseModel):\n"
        "    sku: str\n"
        "\n"
        "def target_fn(req: PriceRequest):\n"
        "    return req.sku.upper()\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "conftest.py").write_text(
        "import pytest\n"
        "\n"
        "@pytest.fixture\n"
        "def client():\n"
        "    return object()\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_pricing.py").write_text(
        "from shop.pricing import target_fn\n"
        "\n"
        "def test_existing_pricing_case(client):\n"
        "    assert target_fn(type('Req', (), {'sku': 'a'})()) == 'A'\n",
        encoding="utf-8",
    )
    analysis = analyze_project(_ctx(tmp_path), AnalyzeProjectInput(target_scope=["target_fn"]))

    bundle = build_source_context_bundle(_ctx(tmp_path), ["target_fn"], analysis)

    by_ref = {snippet.target_ref: snippet for snippet in bundle.snippets}
    target = by_ref["target_fn"]
    assert target.source_kind == "target_source"
    assert target.source_path == "shop/pricing.py"
    assert target.symbol == "target_fn"
    assert target.target == "target_fn"
    assert target.confidence == 1.0
    assert target.retrieval_trace_id

    assert by_ref["model:PriceRequest"].source_kind == "model_schema"
    assert by_ref["fixture:client"].source_kind == "fixture"
    assert by_ref["existing_test:tests/test_pricing.py::test_existing_pricing_case"].source_kind == "existing_test"

    traces = {trace.trace_id: trace for trace in bundle.context_completeness.retrieval_trace}
    assert target.retrieval_trace_id in traces
    assert traces[target.retrieval_trace_id].content_hash == target.content_hash
    assert traces[target.retrieval_trace_id].source_path == "shop/pricing.py"
    assert traces[target.retrieval_trace_id].line_range == {"start": target.start_line, "end": target.end_line}


def test_missing_source_context_target_emits_retrieval_trace(tmp_path):
    (tmp_path / "calc.py").write_text(_SAMPLE, encoding="utf-8")
    analysis = AnalyzeProjectOutput(
        functions=[FunctionInfo(name="target_fn", signature="target_fn(a, b)", file="calc.py")]
    )

    bundle = build_source_context_bundle(_ctx(tmp_path), ["does_not_exist"], analysis)

    [trace] = bundle.context_completeness.retrieval_trace
    assert trace.status == "missing"
    assert trace.source_kind == "target_source"
    assert trace.target == "does_not_exist"
    assert trace.source_path is None
    assert trace.confidence == 0.0
    assert "does_not_exist" in bundle.context_completeness.missing_targets


def test_rg_fallback_resolves_unindexed_function_after_ast_slice(tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "pricing.py").write_text(_SAMPLE, encoding="utf-8")

    bundle = build_source_context_bundle(_ctx(tmp_path), ["target_fn"], AnalyzeProjectOutput())

    assert bundle.context_completeness.status == "complete"
    assert bundle.snippets[0].source_path == "shop/pricing.py"
    assert bundle.snippets[0].symbol == "target_fn"
    assert bundle.snippets[0].retrieval_source == "rg"
    assert "def target_fn" in bundle.source_context_text
    assert "def helper" not in bundle.source_context_text


def test_rg_fallback_unconfirmed_match_stays_incomplete(tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "notes.py").write_text(
        "TARGET_NAME = 'mystery_target'\n"
        "def unrelated():\n"
        "    return TARGET_NAME\n",
        encoding="utf-8",
    )

    bundle = build_source_context_bundle(_ctx(tmp_path), ["mystery_target"], AnalyzeProjectOutput())

    assert bundle.context_completeness.status == "incomplete"
    assert bundle.snippets == []
    assert "mystery_target" in bundle.context_completeness.missing_targets
    assert "TARGET_NAME" not in bundle.source_context_text
    rg_traces = [
        trace
        for trace in bundle.context_completeness.retrieval_trace
        if trace.retrieval_source == "rg" and trace.source_path == "shop/notes.py"
    ]
    assert rg_traces
    assert rg_traces[0].status == "missing"
    assert rg_traces[0].line_range == {"start": 1, "end": 1}
    assert any("not accepted as target source" in note for note in rg_traces[0].risk_notes)


def test_rg_fallback_resolves_route_decorator_to_handler(tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "api.py").write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "\n"
        "@router.get('/items')\n"
        "def list_items():\n"
        "    return []\n",
        encoding="utf-8",
    )

    bundle = build_source_context_bundle(_ctx(tmp_path), ["GET /items"], AnalyzeProjectOutput())

    assert bundle.context_completeness.status == "complete"
    assert bundle.snippets[0].source_path == "shop/api.py"
    assert bundle.snippets[0].symbol == "list_items"
    assert bundle.snippets[0].target == "GET /items"
    assert bundle.snippets[0].retrieval_source == "rg"
    assert "@router.get('/items')" in bundle.source_context_text


def test_path_traversal_target_is_denied(tmp_path):
    # 项目根外放一个“机密”文件，目标越界读它必须失败并标缺失，不能泄漏。
    (tmp_path.parent / "secret.py").write_text("SECRET = 1\n", encoding="utf-8")
    (tmp_path / "calc.py").write_text(_SAMPLE, encoding="utf-8")

    bundle = build_source_context_bundle(_ctx(tmp_path), ["../secret.py"], None)

    assert "SECRET" not in bundle.source_context_text
    assert bundle.context_completeness.context_incomplete is True
    assert "../secret.py" in bundle.context_completeness.missing_targets


def test_total_bytes_budget_truncates(tmp_path):
    for i in range(5):
        (tmp_path / f"m{i}.py").write_text("X = '" + "a" * 2000 + "'\n", encoding="utf-8")
    scope = [f"m{i}.py" for i in range(5)]

    bundle = build_source_context_bundle(_ctx(tmp_path), scope, None, max_total_bytes=3000)

    # 预算 3000 字节，单文件 ~2000，最多放下一个，后续被截断并记风险。
    assert len(bundle.snippets) < 5
    assert any("max_total_bytes" in note for note in bundle.context_completeness.risk_notes)


def test_support_budget_truncation_keeps_target_and_marks_partial(tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "shop" / "pricing.py").write_text(
        "from pydantic import BaseModel\n"
        "\n"
        "class PriceRequest(BaseModel):\n"
        "    sku: str\n"
        "    quantity: int\n"
        "    note: str = '" + "x" * 700 + "'\n"
        "\n"
        "def target_fn(req: PriceRequest):\n"
        "    return req.quantity\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "conftest.py").write_text(
        "import pytest\n"
        "\n"
        "@pytest.fixture\n"
        "def client():\n"
        "    return object()\n",
        encoding="utf-8",
    )
    analysis = analyze_project(_ctx(tmp_path), AnalyzeProjectInput(target_scope=["target_fn"]))

    bundle = build_source_context_bundle(
        _ctx(tmp_path),
        ["target_fn"],
        analysis,
        max_total_bytes=120,
        max_support_snippets=4,
    )

    assert bundle.snippets[0].target_ref == "target_fn"
    assert "def target_fn" in bundle.source_context_text
    assert "class PriceRequest" not in bundle.source_context_text
    assert bundle.context_completeness.status == "partial"
    assert bundle.context_completeness.context_incomplete is True
    traces = {trace.target: trace for trace in bundle.context_completeness.retrieval_trace}
    assert traces["target_fn"].status == "resolved"
    assert traces["model:PriceRequest"].status == "truncated"
    assert any("support context truncated by max_total_bytes" in note for note in bundle.context_completeness.risk_notes)


def test_support_context_order_is_source_kind_priority(tmp_path):
    analysis = AnalyzeProjectOutput(
        models=[
            ModelInfo(
                name="PriceRequest",
                file="shop/api.py",
                fields=[ModelFieldInfo(name="sku")],
            )
        ],
        fixtures=[FixtureInfo(name="client", file="tests/conftest.py", line=3)],
        existing_tests=[
            ExistingTestInfo(
                path="tests/test_pricing.py",
                test_functions=[
                    ExistingTestFunctionInfo(
                        name="test_existing",
                        line=3,
                        estimated_nodeid="tests/test_pricing.py::test_existing",
                    )
                ],
            )
        ],
    )

    (tmp_path / "shop").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "shop" / "api.py").write_text(
        "class PriceRequest:\n"
        "    sku: str\n"
        "\n"
        "def target_fn(req):\n"
        "    return req.sku\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "conftest.py").write_text(
        "def client():\n"
        "    return object()\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_pricing.py").write_text(
        "def test_existing():\n"
        "    assert True\n",
        encoding="utf-8",
    )
    analysis.functions.append(FunctionInfo(name="target_fn", signature="target_fn(req)", file="shop/api.py"))

    bundle = build_source_context_bundle(_ctx(tmp_path), ["target_fn"], analysis)

    assert [snippet.source_kind for snippet in bundle.snippets] == [
        "target_source",
        "model_schema",
        "fixture",
        "existing_test",
    ]


def test_max_snippets_truncation_marks_partial(tmp_path):
    functions = []
    scope = []
    for i in range(4):
        name = f"fn_{i}"
        (tmp_path / f"m{i}.py").write_text(f"def {name}():\n    return {i}\n", encoding="utf-8")
        functions.append(FunctionInfo(name=name, signature=f"{name}()", file=f"m{i}.py"))
        scope.append(name)
    analysis = AnalyzeProjectOutput(functions=functions)

    bundle = build_source_context_bundle(_ctx(tmp_path), scope, analysis, max_snippets=2)

    assert bundle.context_completeness.status == "partial"
    assert bundle.context_completeness.context_incomplete is True
    assert {"fn_2", "fn_3"} <= set(bundle.context_completeness.missing_targets)
    assert any("max_snippets" in note for note in bundle.context_completeness.risk_notes)
