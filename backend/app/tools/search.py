from __future__ import annotations

import fnmatch
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from app.schemas.tools import RgSearchInput, RgSearchMatch, RgSearchOutput
from app.tools.base import ToolContext

_IGNORE = {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache"}


def rg_search(ctx: ToolContext, inp: RgSearchInput) -> RgSearchOutput:
    base = ctx.resolve_read(inp.path)
    warnings: list[str] = []
    if shutil.which("rg"):
        try:
            return _search_with_rg(ctx, base, inp)
        except Exception as exc:
            warnings.append(f"rg search failed, used python fallback: {type(exc).__name__}: {exc}")
    out = _search_with_python(ctx, base, inp)
    out.warnings.extend(warnings)
    return out


def _search_with_rg(ctx: ToolContext, base: Path, inp: RgSearchInput) -> RgSearchOutput:
    cmd = ["rg", "--json", "--no-messages", "--color", "never", "-F"]
    if not inp.case_sensitive:
        cmd.append("-i")
    if inp.glob:
        cmd.extend(["-g", inp.glob])
    cmd.extend(["--", inp.query, str(base)])
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=8,
        check=False,
    )
    if proc.returncode not in {0, 1}:
        raise RuntimeError((proc.stderr or proc.stdout or "rg failed").strip())

    matches: list[RgSearchMatch] = []
    truncated = False
    for raw in proc.stdout.splitlines():
        if not raw.strip():
            continue
        event = json.loads(raw)
        if event.get("type") != "match":
            continue
        data = event.get("data") or {}
        path_text = ((data.get("path") or {}).get("text") or "").strip()
        line_number = int(data.get("line_number") or 0)
        line_text = str(((data.get("lines") or {}).get("text") or "")).rstrip("\r\n")
        if not path_text or line_number < 1:
            continue
        matches.append(_match(ctx, Path(path_text), line_number, line_text, inp.query, engine="rg"))
        if len(matches) >= inp.max_matches:
            truncated = True
            break
    return RgSearchOutput(query=inp.query, matches=matches, truncated=truncated, engine="rg")


def _search_with_python(ctx: ToolContext, base: Path, inp: RgSearchInput) -> RgSearchOutput:
    matches: list[RgSearchMatch] = []
    truncated = False
    needle = inp.query if inp.case_sensitive else inp.query.lower()
    for path in _iter_files(base):
        rel = ctx.relpath(path)
        if inp.glob and not (fnmatch.fnmatch(path.name, inp.glob) or fnmatch.fnmatch(rel, inp.glob)):
            continue
        text = path.read_bytes()[: inp.max_file_bytes].decode("utf-8", errors="replace")
        for index, line in enumerate(text.splitlines(), start=1):
            haystack = line if inp.case_sensitive else line.lower()
            if needle not in haystack:
                continue
            matches.append(_match(ctx, path, index, line, inp.query, engine="python_fallback"))
            if len(matches) >= inp.max_matches:
                truncated = True
                return RgSearchOutput(
                    query=inp.query,
                    matches=matches,
                    truncated=truncated,
                    engine="python_fallback",
                )
    return RgSearchOutput(query=inp.query, matches=matches, truncated=truncated, engine="python_fallback")


def _iter_files(base: Path) -> list[Path]:
    if base.is_file():
        return [base]
    files: list[Path] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        if any(part in _IGNORE for part in path.parts):
            continue
        files.append(path)
    return files


def _match(ctx: ToolContext, path: Path, line_number: int, line_text: str, query: str, *, engine: str) -> RgSearchMatch:
    source_path = ctx.relpath(path)
    content_hash = hashlib.sha256(line_text.encode("utf-8")).hexdigest()
    trace_id = _trace_id(query, source_path, line_number, content_hash)
    return RgSearchMatch(
        source_path=source_path,
        line_number=line_number,
        line_range={"start": line_number, "end": line_number},
        line_text=line_text,
        content_hash=content_hash,
        trace_id=trace_id,
        confidence=0.55 if engine == "rg" else 0.45,
        engine=engine,  # type: ignore[arg-type]
    )


def _trace_id(*parts: object) -> str:
    raw = "\0".join(str(part) for part in parts)
    return "rg-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
