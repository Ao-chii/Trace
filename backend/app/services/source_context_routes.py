from __future__ import annotations

HTTP_ROUTE_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"}


def looks_like_route_target(value: str) -> bool:
    _method, route_path = route_target_parts(value)
    return route_path is not None


def route_target_parts(value: str) -> tuple[str | None, str | None]:
    norm = value.strip()
    if norm.startswith("route:"):
        norm = norm.removeprefix("route:").strip()
    parts = norm.split(maxsplit=1)
    if len(parts) == 2 and parts[0].upper() in HTTP_ROUTE_METHODS:
        path = parts[1].strip()
        return (parts[0].upper(), path) if path.startswith("/") else (None, None)
    return (None, norm) if norm.startswith("/") else (None, None)


def route_path_without_method(value: str) -> str:
    norm = value.strip()
    if norm.startswith("route:"):
        norm = norm.removeprefix("route:").strip()
    parts = norm.split(maxsplit=1)
    if len(parts) == 2 and parts[0].upper() in HTTP_ROUTE_METHODS:
        return parts[1].strip()
    return norm


def route_with_method(value: str) -> str:
    norm = value.strip()
    if norm.startswith("route:"):
        norm = norm.removeprefix("route:").strip()
    return norm