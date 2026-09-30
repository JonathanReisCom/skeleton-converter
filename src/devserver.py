"""Static dev server with no-cache headers.

The stdlib http.server sends no cache headers; Chrome's heuristic caching
(Last-Modified-based) then serves a STALE bundle after it is rebuilt in
place — a previous rig keeps rendering even though every file on disk is
new. The studio builds its own handler on top of `NoCacheHandler`, and a
bundle already on disk can be served with:

    python3 -m src.devserver PORT DIRECTORY
"""

from __future__ import annotations

import http.server
import functools
import sys


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Expires", "0")
        super().end_headers()


def main() -> None:
    port, directory = int(sys.argv[1]), sys.argv[2]
    handler = functools.partial(NoCacheHandler, directory=directory)
    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), handler)
    print(f"serving {directory} on http://localhost:{port}/ (no-cache)", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()