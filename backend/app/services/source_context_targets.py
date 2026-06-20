from __future__ import annotations

from app.services.source_context_routes import looks_like_route_target, route_target_parts


def ast_grep_query_for_target(value: str) -> tuple[str | None, str | None, str | None]:
    method, route_path = route_target_parts(value)
    if route_path:
        query = f"{method} {route_path}" if method else route_path
        return query, "route", method
    symbol = symbol_query_for_target(value)
    return (symbol, "function", None) if symbol else (None, None, None)


def rg_query_for_target(value: str) -> str | None:
    _method, route_path = route_target_parts(value)
    if route_path:
        return route_path
    symbol = symbol_query_for_target(value)
    return symbol or None


def lsp_query_for_target(value: str) -> str:
    norm = value.strip()
    if norm.startswith("route:"):
        norm = norm.removeprefix("route:").strip()
    if looks_like_route_target(norm):
        return ""
    if "::" in norm and norm.split("::", 1)[0].endswith(".py"):
        norm = norm.split("::", 1)[1]
    elif ":" in norm and norm.split(":", 1)[0].endswith(".py"):
        norm = norm.split(":", 1)[1]
    parts = [part for part in norm.split(".") if part]
    if len(parts) == 2 and parts[0][:1].isupper():
        return norm
    return parts[-1] if parts else ""


def symbol_query_for_target(value: str) -> str:
    norm = value.strip()
    if norm.startswith("route:"):
        norm = norm.removeprefix("route:").strip()
    if looks_like_route_target(norm):
        return ""
    if "::" in norm and norm.split("::", 1)[0].endswith(".py"):
        norm = norm.split("::", 1)[1]
    elif ":" in norm and norm.split(":", 1)[0].endswith(".py"):
        norm = norm.split(":", 1)[1]
    return norm.rsplit(".", 1)[-1].strip()
