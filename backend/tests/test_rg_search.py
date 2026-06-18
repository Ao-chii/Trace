from __future__ import annotations

import pytest

from app.core.errors import TraceError
from app.schemas.tools import RgSearchInput
from app.tools import default_registry, rg_search
from app.tools.base import ToolContext


def _ctx(tmp_path):
    write_dir = tmp_path / "tests" / "generated"
    write_dir.mkdir(parents=True, exist_ok=True)
    return ToolContext(root=tmp_path, test_write_dir=write_dir)


def test_rg_search_returns_structured_lexical_evidence(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "api.py").write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "\n"
        "@router.get('/items')\n"
        "def list_items():\n"
        "    return []\n",
        encoding="utf-8",
    )

    out = rg_search(_ctx(tmp_path), RgSearchInput(query="list_items", path="app"))

    assert out.query == "list_items"
    assert out.matches
    match = out.matches[0]
    assert match.source_path == "app/api.py"
    assert match.line_number == 5
    assert match.line_range == {"start": 5, "end": 5}
    assert "def list_items" in match.line_text
    assert match.retrieval_source == "rg"
    assert match.engine in {"rg", "python_fallback"}
    assert match.trace_id.startswith("rg-")
    assert len(match.content_hash) == 64
    assert 0 < match.confidence <= 1


def test_rg_search_respects_glob_and_max_matches(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "one.py").write_text("TARGET = 1\n", encoding="utf-8")
    (tmp_path / "app" / "two.py").write_text("TARGET = 2\n", encoding="utf-8")
    (tmp_path / "app" / "note.txt").write_text("TARGET = ignored\n", encoding="utf-8")

    out = rg_search(_ctx(tmp_path), RgSearchInput(query="TARGET", path="app", glob="*.py", max_matches=1))

    assert len(out.matches) == 1
    assert out.matches[0].source_path.endswith(".py")
    assert out.truncated is True


def test_rg_search_rejects_path_escape(tmp_path):
    (tmp_path.parent / "secret.py").write_text("SECRET = 1\n", encoding="utf-8")

    with pytest.raises(TraceError):
        rg_search(_ctx(tmp_path), RgSearchInput(query="SECRET", path="../secret.py"))


def test_default_registry_exposes_rg_search_tool(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    registry = default_registry()

    assert "rg_search" in registry.names()
    out = registry.call(_ctx(tmp_path), "rg_search", {"query": "add", "path": "."})

    assert out.matches[0].source_path == "calc.py"
