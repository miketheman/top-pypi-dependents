"""Render the static site."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from jinja2 import Environment, PackageLoader, select_autoescape

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

TIERS: tuple[int, ...] = (100, 1000, 10000)


def _environment() -> Environment:
    return Environment(
        loader=PackageLoader("top_pypi_dependents", "templates"),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_site(
    payload: dict[str, Any],
    out_dir: Path,
    *,
    tiers: Sequence[int] = TIERS,
) -> None:
    """Write ``index.html`` and one page per tier into ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    env = _environment()
    shared = {
        "tiers": list(tiers),
        "generated_at": payload["generated_at"],
        "source": payload["source"],
        "project_count": payload["project_count"],
        "edge_count": payload["edge_count"],
    }

    (out_dir / "index.html").write_text(
        env.get_template("index.html.j2").render(**shared), encoding="utf-8"
    )
    table = env.get_template("table.html.j2")
    for tier in tiers:
        (out_dir / f"top-{tier}.html").write_text(
            table.render(tier=tier, rows=payload["rows"][:tier], **shared),
            encoding="utf-8",
        )
