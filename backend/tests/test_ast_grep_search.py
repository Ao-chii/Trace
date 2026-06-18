from __future__ import annotations

import pytest

from app.core.errors import TraceError
from app.schemas.tools import AstGrepSearchInput
from app.tools import ast_grep_search, default_registry
from app.tools.base import ToolContext


def _ctx(tmp_path):
    write_dir = tmp_path / "tests" / "generated"
    write_dir.mkdir(parents=True, exist_ok=True)
    return ToolContext(root=tmp_path, test_write_dir=write_dir)


def test_ast_grep_search_returns_structured_function_evidence(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "pricing.py").write_text(
        "def helper(x):\n"
        "    return x + 1\n"
        "\n"
        "def apply_discount(total, is_member):\n"
        "    if is_member and total >= 100:\n"
        "        return round(total * 0.9, 2)\n"
        "    return round(total, 2)\n",
        encoding="utf-8",
    )

    out = ast_grep_search(_ctx(tmp_path), AstGrepSearchInput(query="apply_discount", kind="function", path="app"))

    assert out.query == "apply_discount"
    assert out.matches
    match = out.matches[0]
    assert match.source_path == "app/pricing.py"
    assert match.node_kind == "function"
    assert match.symbol == "apply_discount"
    assert match.line_range == {"start": 4, "end": 7}
    assert "def apply_discount" in match.matched_text
    assert match.retrieval_source == "ast_grep"
    assert match.engine in {"ast_grep", "python_ast_fallback"}
    assert match.trace_id.startswith("ast-")
    assert len(match.content_hash) == 64
    assert 0 < match.confidence <= 1


def test_ast_grep_search_returns_structured_route_evidence(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "api.py").write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "\n"
        "@router.get('/items/{item_id}')\n"
        "def get_item(item_id: str):\n"
        "    return {'item_id': item_id}\n",
        encoding="utf-8",
    )

    out = ast_grep_search(
        _ctx(tmp_path),
        AstGrepSearchInput(query="GET /items/{item_id}", kind="route", path="app"),
    )

    assert len(out.matches) == 1
    match = out.matches[0]
    assert match.source_path == "app/api.py"
    assert match.node_kind == "route"
    assert match.symbol == "get_item"
    assert match.line_range == {"start": 4, "end": 6}
    assert match.metadata == {"method": "GET", "path": "/items/{item_id}"}


def test_ast_grep_search_rejects_path_escape(tmp_path):
    (tmp_path.parent / "secret.py").write_text("def secret():\n    return 1\n", encoding="utf-8")

    with pytest.raises(TraceError):
        ast_grep_search(_ctx(tmp_path), AstGrepSearchInput(query="secret", kind="function", path="../secret.py"))


def test_default_registry_exposes_ast_grep_search_tool(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    registry = default_registry()

    assert "ast_grep_search" in registry.names()
    out = registry.call(_ctx(tmp_path), "ast_grep_search", {"query": "add", "kind": "function", "path": "."})

    assert out.matches[0].source_path == "calc.py"
