"""Serve the rendered site, re-render on every source change, and reload.

Development only. Renders the committed ``data/`` with ``make site``, serves
it, and polls the package and the data for changes. A change re-renders in a
fresh process, so an edit to ``render.py`` takes effect too, and every open
page reloads itself.

Each render goes into a directory of its own under ``build/serve/``, and the
server switches to it only once the render has succeeded. A failed render is
reported and the last good build stays up; a half-written one is never served.

The reload script is added to each page as it is served, never written into a
build, so nothing here can reach what Pages publishes.
"""

import argparse
import contextlib
import os
import shutil
import subprocess
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import override

ROOT = Path(__file__).resolve().parent.parent
BUILDS = ROOT / "build" / "serve"
WATCHED = (ROOT / "src" / "top_pypi_dependents", ROOT / "data")
MAKE = shutil.which("make") or "make"
BUILD_PATH = "/__build"
POLL_SECONDS = 0.5

# Each page asks which build is current twice a second, and reloads once it is
# no longer the build the page came from. Polled rather than streamed: browsers
# allow six connections per host, and a held stream per tab would use them up.
RELOAD_SCRIPT = """<script>
setInterval(async () => {
    try {
        const response = await fetch("BUILD_PATH", { cache: "no-store" });
        if ((await response.text()) !== "BUILD_ID") location.reload();
    } catch {}
}, 500);
</script>""".replace("BUILD_PATH", BUILD_PATH)


class Handler(SimpleHTTPRequestHandler):
    """Serves the current build, with the reload script and the build it reads."""

    # The build id pages compare against, and its directory. Swapped whole by
    # the watcher, so a request never pairs one build's id with another's files.
    current: tuple[str, Path] = ("", BUILDS)

    @override
    def do_GET(self) -> None:
        self._serve(body=True)

    @override
    def do_HEAD(self) -> None:
        self._serve(body=False)

    def _serve(self, *, body: bool) -> None:
        build, root = self.current
        self.directory = str(root)
        if self.path.split("?", 1)[0] == BUILD_PATH:
            self._send(build.encode(), "text/plain; charset=utf-8", body=body)
            return
        page = Path(self.translate_path(self.path))
        if page.is_dir():
            page /= "index.html"
        if page.suffix == ".html" and page.is_file():
            script = RELOAD_SCRIPT.replace("BUILD_ID", build).encode()
            html = page.read_bytes().replace(b"</body>", script + b"</body>")
            self._send(html, "text/html; charset=utf-8", body=body)
        elif body:
            super().do_GET()
        else:
            super().do_HEAD()

    def _send(self, content: bytes, content_type: str, *, body: bool) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body:
            self.wfile.write(content)

    @override
    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        """Quiet: every asset and poll would print a line. Errors still do."""


def _render(n: int) -> bool:
    out = BUILDS / f"{os.getpid()}-{n}"
    command = (MAKE, "--no-print-directory", "site", f"SITE_OUT={out}")
    if subprocess.run(command, cwd=ROOT, check=False).returncode != 0:  # noqa: S603 -- fixed argv, no shell
        shutil.rmtree(out, ignore_errors=True)
        return False
    previous = Handler.current[1]
    Handler.current = (out.name, out)
    # The build before this one may still be answering a request; any older
    # one is not.
    for old in BUILDS.iterdir():
        if old not in {out, previous}:
            shutil.rmtree(old, ignore_errors=True)
    return True


def _snapshot() -> dict[Path, int]:
    seen = {}
    for root in WATCHED:
        for here, dirs, files in root.walk():
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in files:
                # An editor's temp file can vanish between listing and stat.
                with contextlib.suppress(FileNotFoundError):
                    seen[here / name] = (here / name).stat().st_mtime_ns
    return seen


def _watch(seen: dict[Path, int]) -> None:
    n = 1
    while True:
        time.sleep(POLL_SECONDS)
        now = _snapshot()
        if now != seen:
            seen = now
            n += 1
            _render(n)


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the site and reload on change.")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    shutil.rmtree(BUILDS, ignore_errors=True)
    # Taken before the first render, so an edit saved during it still counts.
    seen = _snapshot()
    if not _render(1):
        return 1
    threading.Thread(target=_watch, args=(seen,), daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    sys.stderr.write(f"Serving http://127.0.0.1:{args.port}/ -- Ctrl-C to stop\n")
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
