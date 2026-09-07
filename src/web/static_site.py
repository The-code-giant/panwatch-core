"""Resolve prerendered public pages without changing application routes."""
from pathlib import Path


def accepts_gzip(header: str) -> bool:
    """Only choose gzip when the client explicitly accepts it."""
    for entry in header.lower().split(","):
        name, *parameters = entry.strip().split(";")
        if name.strip() != "gzip":
            continue
        try:
            quality = next(
                (float(p.strip()[2:]) for p in parameters if p.strip().startswith("q=")),
                1.0,
            )
        except ValueError:
            return False
        return quality > 0
    return False


def resolve_static_page(root: str, route: str) -> tuple[Path | None, int]:
    directory = Path(root).resolve()
    target = (directory / route).resolve()
    if not target.is_relative_to(directory):
        return None, 404
    if target.is_file():
        return target, 200
    page = target / "index.html"
    if page.is_file():
        return page, 200
    if route.strip("/").startswith("research/"):
        missing = directory / "404.html"
        return (missing if missing.is_file() else None), 404
    # Missing assets must not receive HTML with a successful status.
    if Path(route).suffix:
        return None, 404
    shell = directory / "app-shell.html"
    return (shell if shell.is_file() else directory / "index.html"), 200
