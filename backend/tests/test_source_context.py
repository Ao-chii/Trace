from __future__ import annotations

from pathlib import Path

from app.schemas.evaluation import EvaluationEventContract
from app.schemas.tools import (
    AnalyzeProjectOutput,
    ExistingTestInfo,
    FailureDetail,
    FixtureInfo,
    FunctionInfo,
    LspDefinitionOutput,
    ModelFieldInfo,
    ModelInfo,
    RouteInfo,
    TestFunctionInfo as ExistingTestFunctionInfo,
)
from app.services import source_context as source_context_module
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
    assert len(bundle.snippets) == 2
    snip = bundle.snippets[0]
    # 目标函数仍是独立切片：起始行落在 def target_fn 上，不是文件第 1 行。
    assert snip.target_ref == "target_fn"
    assert snip.start_line == 8
    assert snip.end_line == 10
    assert "def target_fn" in bundle.source_context_text
    assert "import math" not in bundle.source_context_text
    helper = bundle.snippets[1]
    assert helper.source_kind == "dependency"
    assert helper.target_ref == "dependency:helper"
    assert helper.symbol == "helper"
    assert "def helper" in bundle.source_context_text


def test_slices_module_constant_used_by_target_function(tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "api.py").write_text(
        "from fastapi import HTTPException\n"
        "\n"
        "_PRICES = {'book': 30.0, 'pen': 2.5}\n"
        "UNUSED = {'apple': 99.0}\n"
        "\n"
        "\n"
        "def get_price(item: str):\n"
        "    if item not in _PRICES:\n"
        "        raise HTTPException(status_code=404, detail='item not found')\n"
        "    return {'item': item, 'total': _PRICES[item]}\n",
        encoding="utf-8",
    )
    analysis = AnalyzeProjectOutput(
        functions=[FunctionInfo(name="get_price", signature="get_price(item: str)", file="shop/api.py")]
    )

    bundle = build_source_context_bundle(_ctx(tmp_path), ["get_price"], analysis)

    assert bundle.context_completeness.status == "complete"
    assert [snippet.source_kind for snippet in bundle.snippets] == ["target_source", "dependency"]
    dependency = bundle.snippets[1]
    assert dependency.path == "shop/api.py"
    assert dependency.symbol == "_PRICES"
    assert dependency.target_ref == "dependency:_PRICES"
    assert "_PRICES = {'book': 30.0, 'pen': 2.5}" in bundle.source_context_text
    assert "UNUSED" not in bundle.source_context_text

def test_slices_cross_file_direct_import_dependency(tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "helpers.py").write_text(
        "def normalize_price(total):\n"
        "    return round(total, 2)\n"
        "\n"
        "\n"
        "def unrelated_helper():\n"
        "    return 'unused'\n",
        encoding="utf-8",
    )
    (tmp_path / "shop" / "pricing.py").write_text(
        "from shop.helpers import normalize_price\n"
        "\n"
        "\n"
        "def target_fn(total):\n"
        "    return normalize_price(total) + 1\n",
        encoding="utf-8",
    )
    analysis = AnalyzeProjectOutput(
        functions=[FunctionInfo(name="target_fn", signature="target_fn(total)", file="shop/pricing.py")]
    )

    bundle = build_source_context_bundle(_ctx(tmp_path), ["target_fn"], analysis)

    assert bundle.context_completeness.status == "complete"
    assert [snippet.source_kind for snippet in bundle.snippets] == ["target_source", "dependency"]
    dependency = bundle.snippets[1]
    assert dependency.path == "shop/helpers.py"
    assert dependency.symbol == "normalize_price"
    assert dependency.target_ref == "dependency:normalize_price"
    assert dependency.retrieval_source == "analysis_ast"
    assert "def normalize_price" in bundle.source_context_text
    assert "def unrelated_helper" not in bundle.source_context_text


def test_slices_relative_import_alias_dependency(tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "helpers.py").write_text(
        "def normalize_price(total):\n"
        "    return round(total, 2)\n",
        encoding="utf-8",
    )
    (tmp_path / "shop" / "pricing.py").write_text(
        "from .helpers import normalize_price as normalize\n"
        "\n"
        "\n"
        "def target_fn(total):\n"
        "    return normalize(total) + 1\n",
        encoding="utf-8",
    )
    analysis = AnalyzeProjectOutput(
        functions=[FunctionInfo(name="target_fn", signature="target_fn(total)", file="shop/pricing.py")]
    )

    bundle = build_source_context_bundle(_ctx(tmp_path), ["target_fn"], analysis)

    assert [snippet.source_kind for snippet in bundle.snippets] == ["target_source", "dependency"]
    assert bundle.snippets[1].path == "shop/helpers.py"
    assert bundle.snippets[1].symbol == "normalize_price"


def test_slices_module_alias_attribute_dependency(tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "helpers.py").write_text(
        "def normalize_price(total):\n"
        "    return round(total, 2)\n"
        "\n"
        "\n"
        "def unrelated_helper():\n"
        "    return 'unused'\n",
        encoding="utf-8",
    )
    (tmp_path / "shop" / "pricing.py").write_text(
        "import shop.helpers as helpers\n"
        "\n"
        "\n"
        "def target_fn(total):\n"
        "    return helpers.normalize_price(total) + 1\n",
        encoding="utf-8",
    )
    analysis = AnalyzeProjectOutput(
        functions=[FunctionInfo(name="target_fn", signature="target_fn(total)", file="shop/pricing.py")]
    )

    bundle = build_source_context_bundle(_ctx(tmp_path), ["target_fn"], analysis)

    assert bundle.context_completeness.status == "complete"
    assert [snippet.source_kind for snippet in bundle.snippets] == ["target_source", "dependency"]
    dependency = bundle.snippets[1]
    assert dependency.path == "shop/helpers.py"
    assert dependency.symbol == "normalize_price"
    assert dependency.target_ref == "dependency:normalize_price"
    assert "def normalize_price" in bundle.source_context_text
    assert "def unrelated_helper" not in bundle.source_context_text


def test_slices_from_package_module_attribute_dependency(tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "helpers.py").write_text(
        "def normalize_price(total):\n"
        "    return round(total, 2)\n",
        encoding="utf-8",
    )
    (tmp_path / "shop" / "pricing.py").write_text(
        "from shop import helpers\n"
        "\n"
        "\n"
        "def target_fn(total):\n"
        "    return helpers.normalize_price(total) + 1\n",
        encoding="utf-8",
    )
    analysis = AnalyzeProjectOutput(
        functions=[FunctionInfo(name="target_fn", signature="target_fn(total)", file="shop/pricing.py")]
    )

    bundle = build_source_context_bundle(_ctx(tmp_path), ["target_fn"], analysis)

    assert bundle.context_completeness.status == "complete"
    assert [snippet.source_kind for snippet in bundle.snippets] == ["target_source", "dependency"]
    assert bundle.snippets[1].path == "shop/helpers.py"
    assert bundle.snippets[1].symbol == "normalize_price"


def test_slices_multihop_same_file_dependency(tmp_path):
    (tmp_path / "calc.py").write_text(
        "def cents(total):\n"
        "    return round(total * 100)\n"
        "\n"
        "\n"
        "def helper(total):\n"
        "    return cents(total) + 1\n"
        "\n"
        "\n"
        "def target_fn(total):\n"
        "    return helper(total)\n",
        encoding="utf-8",
    )
    analysis = AnalyzeProjectOutput(
        functions=[FunctionInfo(name="target_fn", signature="target_fn(total)", file="calc.py")]
    )

    bundle = build_source_context_bundle(_ctx(tmp_path), ["target_fn"], analysis)

    assert bundle.context_completeness.status == "complete"
    assert [snippet.source_kind for snippet in bundle.snippets] == ["target_source", "dependency", "dependency"]
    assert [snippet.symbol for snippet in bundle.snippets] == ["target_fn", "helper", "cents"]
    assert "def cents" in bundle.source_context_text


def test_multihop_dependency_depth_can_be_limited(tmp_path):
    (tmp_path / "calc.py").write_text(
        "def cents(total):\n"
        "    return round(total * 100)\n"
        "\n"
        "\n"
        "def helper(total):\n"
        "    return cents(total) + 1\n"
        "\n"
        "\n"
        "def target_fn(total):\n"
        "    return helper(total)\n",
        encoding="utf-8",
    )
    analysis = AnalyzeProjectOutput(
        functions=[FunctionInfo(name="target_fn", signature="target_fn(total)", file="calc.py")]
    )

    bundle = build_source_context_bundle(_ctx(tmp_path), ["target_fn"], analysis, max_dependency_depth=1)

    assert bundle.context_completeness.status == "complete"
    assert [snippet.symbol for snippet in bundle.snippets] == ["target_fn", "helper"]
    assert "def cents" not in bundle.source_context_text


def test_multihop_dependency_snippet_budget_marks_partial(tmp_path):
    (tmp_path / "calc.py").write_text(
        "def cents(total):\n"
        "    return round(total * 100)\n"
        "\n"
        "\n"
        "def helper(total):\n"
        "    return cents(total) + 1\n"
        "\n"
        "\n"
        "def target_fn(total):\n"
        "    return helper(total)\n",
        encoding="utf-8",
    )
    analysis = AnalyzeProjectOutput(
        functions=[FunctionInfo(name="target_fn", signature="target_fn(total)", file="calc.py")]
    )

    bundle = build_source_context_bundle(
        _ctx(tmp_path),
        ["target_fn"],
        analysis,
        max_dependency_snippets=1,
    )

    assert [snippet.symbol for snippet in bundle.snippets] == ["target_fn", "helper"]
    assert "def cents" not in bundle.source_context_text
    assert bundle.context_completeness.status == "partial"
    trace = next(
        trace
        for trace in bundle.context_completeness.retrieval_trace
        if trace.source_kind == "dependency" and trace.symbol == "cents"
    )
    assert trace.status == "truncated"


def test_multihop_dependency_cycle_does_not_duplicate_target(tmp_path):
    (tmp_path / "calc.py").write_text(
        "def helper(total):\n"
        "    return target_fn(total) + 1\n"
        "\n"
        "\n"
        "def target_fn(total):\n"
        "    if total <= 0:\n"
        "        return 0\n"
        "    return helper(total)\n",
        encoding="utf-8",
    )
    analysis = AnalyzeProjectOutput(
        functions=[FunctionInfo(name="target_fn", signature="target_fn(total)", file="calc.py")]
    )

    bundle = build_source_context_bundle(_ctx(tmp_path), ["target_fn"], analysis)

    assert bundle.context_completeness.status == "complete"
    assert [snippet.symbol for snippet in bundle.snippets] == ["target_fn", "helper"]
    assert bundle.source_context_text.count("def target_fn") == 1


def test_missing_second_hop_dependency_marks_partial(tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "helpers.py").write_text(
        "from shop.money import cents\n"
        "\n"
        "\n"
        "def normalize_price(total):\n"
        "    return cents(total) / 100\n",
        encoding="utf-8",
    )
    (tmp_path / "shop" / "pricing.py").write_text(
        "from shop.helpers import normalize_price\n"
        "\n"
        "\n"
        "def target_fn(total):\n"
        "    return normalize_price(total) + 1\n",
        encoding="utf-8",
    )
    analysis = AnalyzeProjectOutput(
        functions=[FunctionInfo(name="target_fn", signature="target_fn(total)", file="shop/pricing.py")]
    )

    bundle = build_source_context_bundle(_ctx(tmp_path), ["target_fn"], analysis)

    assert [snippet.symbol for snippet in bundle.snippets] == ["target_fn", "normalize_price"]
    assert bundle.context_completeness.status == "partial"
    trace = next(
        trace
        for trace in bundle.context_completeness.retrieval_trace
        if trace.source_kind == "dependency" and trace.symbol == "cents"
    )
    assert trace.status == "error"
    assert trace.source_path == "shop/money.py"


def test_slices_self_method_dependency(tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "pricing.py").write_text(
        "class PriceCalculator:\n"
        "    def normalize(self, total):\n"
        "        return round(total, 2)\n"
        "\n"
        "    def total(self, subtotal):\n"
        "        return self.normalize(subtotal)\n",
        encoding="utf-8",
    )

    bundle = build_source_context_bundle(_ctx(tmp_path), ["shop/pricing.py::PriceCalculator.total"])

    assert bundle.context_completeness.status == "complete"
    assert [snippet.source_kind for snippet in bundle.snippets] == ["target_source", "dependency"]
    assert bundle.snippets[1].symbol == "PriceCalculator.normalize"
    assert bundle.snippets[1].target_ref == "dependency:PriceCalculator.normalize"
    assert "def normalize" in bundle.source_context_text


def test_slices_class_name_method_dependency(tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "pricing.py").write_text(
        "class PriceCalculator:\n"
        "    @staticmethod\n"
        "    def normalize(total):\n"
        "        return round(total, 2)\n"
        "\n"
        "    def total(self, subtotal):\n"
        "        return PriceCalculator.normalize(subtotal)\n",
        encoding="utf-8",
    )

    bundle = build_source_context_bundle(_ctx(tmp_path), ["shop/pricing.py::PriceCalculator.total"])

    assert bundle.context_completeness.status == "complete"
    assert [snippet.source_kind for snippet in bundle.snippets] == ["target_source", "dependency"]
    assert bundle.snippets[1].symbol == "PriceCalculator.normalize"
    assert "@staticmethod" in bundle.source_context_text


def test_multihop_self_method_dependency(tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "pricing.py").write_text(
        "class PriceCalculator:\n"
        "    def cents(self, total):\n"
        "        return round(total * 100)\n"
        "\n"
        "    def normalize(self, total):\n"
        "        return self.cents(total) / 100\n"
        "\n"
        "    def total(self, subtotal):\n"
        "        return self.normalize(subtotal)\n",
        encoding="utf-8",
    )

    bundle = build_source_context_bundle(_ctx(tmp_path), ["shop/pricing.py::PriceCalculator.total"])

    assert [snippet.symbol for snippet in bundle.snippets] == [
        "PriceCalculator.total",
        "PriceCalculator.normalize",
        "PriceCalculator.cents",
    ]


def test_missing_module_attribute_dependency_marks_partial(tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "pricing.py").write_text(
        "import shop.helpers as helpers\n"
        "\n"
        "\n"
        "def target_fn(total):\n"
        "    return helpers.normalize_price(total) + 1\n",
        encoding="utf-8",
    )
    analysis = AnalyzeProjectOutput(
        functions=[FunctionInfo(name="target_fn", signature="target_fn(total)", file="shop/pricing.py")]
    )

    bundle = build_source_context_bundle(_ctx(tmp_path), ["target_fn"], analysis)

    assert [snippet.source_kind for snippet in bundle.snippets] == ["target_source"]
    assert bundle.context_completeness.status == "partial"
    trace = next(trace for trace in bundle.context_completeness.retrieval_trace if trace.source_kind == "dependency")
    assert trace.status == "error"
    assert trace.source_path == "shop/helpers.py"
    assert trace.symbol == "normalize_price"
    assert any("cross-file direct dependency source could not be read" in note for note in bundle.context_completeness.risk_notes)


def test_missing_cross_file_direct_import_dependency_marks_partial(tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "pricing.py").write_text(
        "from shop.helpers import normalize_price\n"
        "\n"
        "\n"
        "def target_fn(total):\n"
        "    return normalize_price(total) + 1\n",
        encoding="utf-8",
    )
    analysis = AnalyzeProjectOutput(
        functions=[FunctionInfo(name="target_fn", signature="target_fn(total)", file="shop/pricing.py")]
    )

    bundle = build_source_context_bundle(_ctx(tmp_path), ["target_fn"], analysis)

    assert [snippet.source_kind for snippet in bundle.snippets] == ["target_source"]
    assert bundle.context_completeness.status == "partial"
    trace = next(trace for trace in bundle.context_completeness.retrieval_trace if trace.source_kind == "dependency")
    assert trace.status == "error"
    assert trace.source_path == "shop/helpers.py"
    assert trace.symbol == "normalize_price"
    assert any("cross-file direct dependency source could not be read" in note for note in bundle.context_completeness.risk_notes)


def test_external_direct_import_call_does_not_mark_dependency_missing(tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "pricing.py").write_text(
        "from decimal import Decimal\n"
        "\n"
        "\n"
        "def target_fn(total):\n"
        "    return Decimal(total)\n",
        encoding="utf-8",
    )
    analysis = AnalyzeProjectOutput(
        functions=[FunctionInfo(name="target_fn", signature="target_fn(total)", file="shop/pricing.py")]
    )

    bundle = build_source_context_bundle(_ctx(tmp_path), ["target_fn"], analysis)

    assert bundle.context_completeness.status == "complete"
    assert [snippet.source_kind for snippet in bundle.snippets] == ["target_source"]
    assert not any(trace.source_kind == "dependency" for trace in bundle.context_completeness.retrieval_trace)


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


def test_source_context_includes_lsp_reference_evidence(tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "shop" / "pricing.py").write_text(_SAMPLE, encoding="utf-8")
    (tmp_path / "tests" / "test_pricing.py").write_text(
        "from shop.pricing import target_fn\n"
        "\n"
        "def test_existing_pricing_case():\n"
        "    assert target_fn(2, 3) == 7\n",
        encoding="utf-8",
    )
    analysis = AnalyzeProjectOutput(
        functions=[FunctionInfo(name="target_fn", signature="target_fn(a, b)", file="shop/pricing.py")]
    )

    bundle = build_source_context_bundle(_ctx(tmp_path), ["target_fn"], analysis)

    assert bundle.context_completeness.status == "complete"
    reference = next(snippet for snippet in bundle.snippets if snippet.source_kind == "reference")
    assert reference.path == "tests/test_pricing.py"
    assert reference.target_ref == "reference:target_fn"
    assert reference.retrieval_source == "lsp"
    assert reference.start_line == 4
    assert "target_fn(2, 3)" in bundle.source_context_text
    trace = next(trace for trace in bundle.context_completeness.retrieval_trace if trace.source_kind == "reference")
    assert trace.status == "resolved"
    assert trace.source_path == "tests/test_pricing.py"
    assert trace.line_range == {"start": 4, "end": 4}


def test_lsp_reference_budget_marks_partial(tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "shop" / "pricing.py").write_text(_SAMPLE, encoding="utf-8")
    (tmp_path / "tests" / "test_pricing.py").write_text(
        "from shop.pricing import target_fn\n"
        "\n"
        "def test_one():\n"
        "    assert target_fn(1, 2) == 3\n"
        "\n"
        "def test_two():\n"
        "    assert target_fn(2, 3) == 7\n",
        encoding="utf-8",
    )
    analysis = AnalyzeProjectOutput(
        functions=[FunctionInfo(name="target_fn", signature="target_fn(a, b)", file="shop/pricing.py")]
    )

    bundle = build_source_context_bundle(_ctx(tmp_path), ["target_fn"], analysis, max_reference_snippets=1)

    references = [snippet for snippet in bundle.snippets if snippet.source_kind == "reference"]
    assert len(references) == 1
    assert bundle.context_completeness.status == "partial"
    truncated = [
        trace
        for trace in bundle.context_completeness.retrieval_trace
        if trace.source_kind == "reference" and trace.status == "truncated"
    ]
    assert truncated
    assert any("reference context truncated by max_reference_snippets" in note for note in bundle.context_completeness.risk_notes)


def test_source_context_includes_failure_context_evidence(tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "tests" / "generated").mkdir(parents=True)
    (tmp_path / "shop" / "pricing.py").write_text(_SAMPLE, encoding="utf-8")
    (tmp_path / "tests" / "generated" / "test_generated.py").write_text(
        "from shop.pricing import target_fn\n"
        "\n"
        "def test_target_fn_failure():\n"
        "    assert target_fn(2, 3) == 999\n",
        encoding="utf-8",
    )
    analysis = AnalyzeProjectOutput(
        functions=[FunctionInfo(name="target_fn", signature="target_fn(a, b)", file="shop/pricing.py")]
    )

    bundle = build_source_context_bundle(
        _ctx(tmp_path),
        ["target_fn"],
        analysis,
        failure_details=[
            FailureDetail(
                nodeid="tests/generated/test_generated.py::test_target_fn_failure",
                file="tests/generated/test_generated.py",
                line=4,
                exc_type="AssertionError",
                message="assert 7 == 999",
                traceback="E       assert 7 == 999",
                failure_type="assertion",
            )
        ],
    )

    assert bundle.context_completeness.status == "complete"
    assert [snippet.source_kind for snippet in bundle.snippets] == ["target_source", "dependency", "failure_context"]
    failure = bundle.snippets[2]
    assert failure.path == "tests/generated/test_generated.py"
    assert failure.target_ref == "tests/generated/test_generated.py::test_target_fn_failure"
    assert failure.retrieval_source == "pytest_result"
    assert failure.start_line == 4
    assert "failure_context:tests/generated/test_generated.py::test_target_fn_failure" in bundle.source_context_text
    assert "AssertionError" in bundle.source_context_text
    assert "E       assert 7 == 999" in bundle.source_context_text
    trace = next(t for t in bundle.context_completeness.retrieval_trace if t.source_kind == "failure_context")
    assert trace.status == "resolved"
    assert trace.source_path == "tests/generated/test_generated.py"
    assert trace.line_range == {"start": 4, "end": 4}


def test_source_context_includes_evaluation_event_failure_context(tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "tests" / "generated").mkdir(parents=True)
    (tmp_path / "shop" / "pricing.py").write_text(_SAMPLE, encoding="utf-8")
    analysis = AnalyzeProjectOutput(
        functions=[FunctionInfo(name="target_fn", signature="target_fn(a, b)", file="shop/pricing.py")]
    )

    event = EvaluationEventContract(
        event_id="evt-flaky-1",
        event_type="flaky_clean_replay",
        severity="blocking",
        scope="clean_run",
        experiment_id="exp-1",
        clean_run_id="clean-1",
        stable_code="flaky_clean_replay",
        reason="run 2 mismatch: status changed",
        source_ids={"baseline_replay_id": "replay-1"},
        nodeids=["tests/generated/test_generated.py::test_target_fn_failure"],
        payload={"required_runs": 3, "actual_runs": 3},
    )

    bundle = build_source_context_bundle(
        _ctx(tmp_path),
        ["target_fn"],
        analysis,
        evaluation_events=[event],
    )

    failure = next(snippet for snippet in bundle.snippets if snippet.retrieval_source == "evaluation_event")
    assert failure.source_kind == "failure_context"
    assert failure.target_ref == "evt-flaky-1"
    assert failure.path == "tests/generated/evaluation_events.txt"
    assert "event_id: evt-flaky-1" in bundle.source_context_text
    assert "event_type: flaky_clean_replay" in bundle.source_context_text
    assert "source_id.baseline_replay_id: replay-1" in bundle.source_context_text
    trace = next(t for t in bundle.context_completeness.retrieval_trace if t.retrieval_source == "evaluation_event")
    assert trace.status == "resolved"
    assert trace.target == "evt-flaky-1"


def test_source_context_filters_audit_only_evaluation_events_from_prompt(tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "pricing.py").write_text(_SAMPLE, encoding="utf-8")
    analysis = AnalyzeProjectOutput(
        functions=[FunctionInfo(name="target_fn", signature="target_fn(a, b)", file="shop/pricing.py")]
    )
    event = EvaluationEventContract(
        event_id="evt-provider-1",
        event_type="provider_failure",
        severity="blocking",
        scope="experiment",
        experiment_id="exp-1",
        stable_code="provider_failure",
        reason="LLM provider failed with private transport details",
    )

    bundle = build_source_context_bundle(
        _ctx(tmp_path),
        ["target_fn"],
        analysis,
        evaluation_events=[event],
    )

    assert "evt-provider-1" not in bundle.source_context_text
    assert "provider_failure" not in bundle.source_context_text
    assert not any(snippet.retrieval_source == "evaluation_event" for snippet in bundle.snippets)
    assert any("event is audit-only" in note for note in bundle.context_completeness.risk_notes)

def test_source_context_marks_evaluation_event_truncation_partial(tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "pricing.py").write_text(_SAMPLE, encoding="utf-8")
    analysis = AnalyzeProjectOutput(
        functions=[FunctionInfo(name="target_fn", signature="target_fn(a, b)", file="shop/pricing.py")]
    )
    events = [
        EvaluationEventContract(
            event_id=f"evt-replay-{index}",
            event_type="replay_failure",
            severity="info",
            scope="replay",
            experiment_id="exp-1",
            clean_run_id="clean-1",
            replay_id=f"replay-{index}",
            bug_variant_id=f"variant-{index}",
            stable_code="replay_failure",
            reason="variant replay did not capture bug",
        )
        for index in range(2)
    ]

    bundle = build_source_context_bundle(
        _ctx(tmp_path),
        ["target_fn"],
        analysis,
        evaluation_events=events,
        max_evaluation_events=1,
    )

    assert bundle.context_completeness.status == "partial"
    assert any("evaluation event context truncated by max_evaluation_events" in note for note in bundle.context_completeness.risk_notes)
    traces = [
        trace
        for trace in bundle.context_completeness.retrieval_trace
        if trace.retrieval_source == "evaluation_event"
    ]
    assert [trace.status for trace in traces] == ["truncated", "resolved"]


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

    traces = bundle.context_completeness.retrieval_trace
    assert any(
        trace.status == "missing"
        and trace.retrieval_source == "ast_grep"
        and trace.target == "does_not_exist"
        and trace.confidence == 0.0
        for trace in traces
    )
    assert any(
        trace.status == "missing"
        and trace.retrieval_source == "analysis_ast"
        and trace.target == "does_not_exist"
        and trace.source_path is None
        for trace in traces
    )
    assert "does_not_exist" in bundle.context_completeness.missing_targets


def test_ast_grep_fallback_resolves_unindexed_function_after_structural_match(tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "pricing.py").write_text(_SAMPLE, encoding="utf-8")

    bundle = build_source_context_bundle(_ctx(tmp_path), ["target_fn"], AnalyzeProjectOutput())

    assert bundle.context_completeness.status == "complete"
    assert bundle.snippets[0].source_path == "shop/pricing.py"
    assert bundle.snippets[0].symbol == "target_fn"
    assert bundle.snippets[0].retrieval_source == "lsp"
    assert "def target_fn" in bundle.source_context_text
    assert bundle.snippets[1].source_kind == "dependency"
    assert bundle.snippets[1].symbol == "helper"
    assert "def helper" in bundle.source_context_text


def test_lsp_fallback_resolves_unindexed_function_before_ast_grep(tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "pricing.py").write_text(_SAMPLE, encoding="utf-8")

    bundle = build_source_context_bundle(_ctx(tmp_path), ["target_fn"], AnalyzeProjectOutput())

    target = bundle.snippets[0]
    assert target.retrieval_source == "lsp"
    traces = {trace.trace_id: trace for trace in bundle.context_completeness.retrieval_trace}
    assert target.retrieval_trace_id in traces
    assert traces[target.retrieval_trace_id].retrieval_source == "lsp"
    assert traces[target.retrieval_trace_id].status == "resolved"


def test_lsp_missing_then_ast_grep_success_adds_risk_note(tmp_path, monkeypatch):
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "pricing.py").write_text(_SAMPLE, encoding="utf-8")

    def fake_lsp_definition(ctx, inp):
        return LspDefinitionOutput(
            query=inp.query,
            status="missing",
            engine="python_ast_fallback",
            warnings=["pyright-langserver unavailable; used python AST fallback"],
        )

    monkeypatch.setattr(source_context_module, "lsp_definition", fake_lsp_definition)

    bundle = build_source_context_bundle(_ctx(tmp_path), ["target_fn"], AnalyzeProjectOutput())

    assert bundle.context_completeness.status == "complete"
    assert bundle.snippets[0].retrieval_source == "ast_grep"
    assert any("lsp definition missing" in note for note in bundle.context_completeness.risk_notes)


def test_target_resolution_ranks_multiple_provider_candidates_by_confidence(tmp_path, monkeypatch):
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "lsp_bad.py").write_text("def target_fn():\n    return 'bad'\n", encoding="utf-8")
    (tmp_path / "shop" / "ast_good.py").write_text("def target_fn():\n    return 'good'\n", encoding="utf-8")

    def fake_lsp(ctx, raw):
        return (
            source_context_module._Target(
                raw=raw,
                rel_path="shop/lsp_bad.py",
                symbol="target_fn",
                retrieval_source="lsp",
                confidence=0.3,
            ),
            [],
        )

    def fake_ast_grep(ctx, raw):
        return (
            source_context_module._Target(
                raw=raw,
                rel_path="shop/ast_good.py",
                symbol="target_fn",
                retrieval_source="ast_grep",
                confidence=0.85,
            ),
            [],
        )

    monkeypatch.setattr(source_context_module, "_resolve_one_with_lsp", fake_lsp)
    monkeypatch.setattr(source_context_module, "_resolve_one_with_ast_grep", fake_ast_grep)
    monkeypatch.setattr(source_context_module, "_resolve_one_with_rg", lambda ctx, raw: (None, []))

    bundle = build_source_context_bundle(_ctx(tmp_path), ["target_fn"], AnalyzeProjectOutput())

    assert bundle.snippets[0].source_path == "shop/ast_good.py"
    assert bundle.snippets[0].retrieval_source == "ast_grep"
    assert "return 'good'" in bundle.source_context_text
    assert "return 'bad'" not in bundle.source_context_text


def test_direct_dependency_budget_keeps_target_and_marks_partial(tmp_path):
    (tmp_path / "calc.py").write_text(_SAMPLE, encoding="utf-8")
    analysis = AnalyzeProjectOutput(
        functions=[FunctionInfo(name="target_fn", signature="target_fn(a, b)", file="calc.py")]
    )

    bundle = build_source_context_bundle(_ctx(tmp_path), ["target_fn"], analysis, max_total_bytes=90)

    assert bundle.snippets[0].target_ref == "target_fn"
    assert [snippet.source_kind for snippet in bundle.snippets] == ["target_source"]
    assert "def target_fn" in bundle.source_context_text
    assert "def helper" not in bundle.source_context_text
    assert bundle.context_completeness.status == "partial"
    dependency_trace = next(
        trace for trace in bundle.context_completeness.retrieval_trace if trace.source_kind == "dependency"
    )
    assert dependency_trace.status == "truncated"
    assert any("direct dependency context truncated by max_total_bytes" in note for note in bundle.context_completeness.risk_notes)


def test_multiple_targets_keep_all_target_sources_before_dependency_budget(tmp_path):
    (tmp_path / "calc.py").write_text(
        "def helper_a():\n"
        "    return 'xxxxxxxxxxxxxxxxxxxx'\n"
        "\n"
        "\n"
        "def target_a():\n"
        "    return helper_a()\n"
        "\n"
        "\n"
        "def target_b():\n"
        "    return 2\n",
        encoding="utf-8",
    )
    analysis = AnalyzeProjectOutput(
        functions=[
            FunctionInfo(name="target_a", signature="target_a()", file="calc.py"),
            FunctionInfo(name="target_b", signature="target_b()", file="calc.py"),
        ]
    )

    bundle = build_source_context_bundle(
        _ctx(tmp_path),
        ["target_a", "target_b"],
        analysis,
        max_total_bytes=95,
    )

    assert [snippet.target_ref for snippet in bundle.snippets] == ["target_a", "target_b"]
    assert "def target_a" in bundle.source_context_text
    assert "def target_b" in bundle.source_context_text
    assert "def helper_a" not in bundle.source_context_text
    assert "target_b" not in bundle.context_completeness.missing_targets
    assert bundle.context_completeness.status == "partial"
    dependency_trace = next(
        trace
        for trace in bundle.context_completeness.retrieval_trace
        if trace.source_kind == "dependency" and trace.symbol == "helper_a"
    )
    assert dependency_trace.status == "truncated"


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
    ast_traces = [trace for trace in bundle.context_completeness.retrieval_trace if trace.retrieval_source == "ast_grep"]
    assert ast_traces
    assert ast_traces[0].status == "missing"
    lsp_traces = [trace for trace in bundle.context_completeness.retrieval_trace if trace.retrieval_source == "lsp"]
    assert lsp_traces
    assert lsp_traces[0].status == "missing"
    rg_traces = [
        trace
        for trace in bundle.context_completeness.retrieval_trace
        if trace.retrieval_source == "rg" and trace.source_path == "shop/notes.py"
    ]
    assert rg_traces
    assert rg_traces[0].status == "missing"
    assert rg_traces[0].line_range == {"start": 1, "end": 1}
    assert any("not accepted as target source" in note for note in rg_traces[0].risk_notes)


def test_ast_grep_fallback_resolves_route_decorator_to_handler(tmp_path):
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
    assert bundle.snippets[0].retrieval_source == "ast_grep"
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


def test_support_context_deduplicates_before_support_budget(tmp_path):
    analysis = AnalyzeProjectOutput(
        functions=[FunctionInfo(name="target_fn", signature="target_fn()", file="shop/api.py")],
        models=[
            ModelInfo(name="PriceRequest", file="shop/api.py", fields=[ModelFieldInfo(name="sku")]),
            ModelInfo(name="PriceRequest", file="shop/api.py", fields=[ModelFieldInfo(name="sku")]),
        ],
        fixtures=[FixtureInfo(name="client", file="tests/conftest.py", line=2)],
    )

    (tmp_path / "shop").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "shop" / "api.py").write_text(
        "class PriceRequest:\n"
        "    sku: str\n"
        "\n"
        "def target_fn():\n"
        "    return PriceRequest()\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "conftest.py").write_text(
        "def client():\n"
        "    return object()\n",
        encoding="utf-8",
    )

    bundle = build_source_context_bundle(
        _ctx(tmp_path),
        ["target_fn"],
        analysis,
        max_support_snippets=2,
        max_dependency_snippets=0,
        max_reference_snippets=0,
    )

    assert [snippet.source_kind for snippet in bundle.snippets] == [
        "target_source",
        "model_schema",
        "fixture",
    ]
    assert not any(
        trace.source_kind == "fixture" and trace.status == "truncated"
        for trace in bundle.context_completeness.retrieval_trace
    )
    assert any(
        trace.source_kind == "model_schema"
        and any("support context deduplicated by evidence key" in note for note in trace.risk_notes)
        for trace in bundle.context_completeness.retrieval_trace
    )


def test_support_context_deduplicates_cross_source_with_trace(tmp_path):
    analysis = AnalyzeProjectOutput(
        functions=[FunctionInfo(name="target_fn", signature="target_fn()", file="shop/api.py")],
        fixtures=[FixtureInfo(name="client", file="tests/conftest.py", line=2)],
        existing_tests=[
            ExistingTestInfo(
                path="tests/conftest.py",
                test_functions=[
                    ExistingTestFunctionInfo(
                        name="client",
                        line=1,
                        estimated_nodeid="tests/conftest.py::client",
                    )
                ],
            )
        ],
    )

    (tmp_path / "shop").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "shop" / "api.py").write_text(
        "def target_fn():\n"
        "    return 1\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "conftest.py").write_text(
        "def client():\n"
        "    return object()\n",
        encoding="utf-8",
    )

    bundle = build_source_context_bundle(
        _ctx(tmp_path),
        ["target_fn"],
        analysis,
        max_dependency_snippets=0,
        max_reference_snippets=0,
    )

    assert [snippet.source_kind for snippet in bundle.snippets] == ["target_source", "fixture"]
    assert bundle.source_context_text.count("def client") == 1
    assert bundle.context_completeness.status == "complete"
    trace = next(
        trace
        for trace in bundle.context_completeness.retrieval_trace
        if trace.target == "existing_test:tests/conftest.py::client"
    )
    assert trace.status == "resolved"
    assert trace.source_kind == "existing_test"
    assert any("support context deduplicated by evidence key" in note for note in trace.risk_notes)


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
