#!/usr/bin/env python3
"""
Local web UI for the technical-analysis skill. Standard library only; listens on 127.0.0.1.

  python ui/server.py                    # http://127.0.0.1:8876 and opens the browser
  python ui/server.py --port 9000 --no-browser

Routes
  GET /                       ui/index.html
  GET /<file>                 static files from ui/ only (.html .js .css .svg .png .ico)
  GET /api/analyze?symbol=AAPL&horizon=swing|position[&refresh=1]
                              runs analyze.run_analysis (the same code as scripts/analyze.py) and
                              returns {"metrics": ..., "chart": ...}; errors return {"error": ...}

Connection settings come from the environment exactly as for the scripts. On Windows, FMP_*
values saved with `setx` are also read from the registry, so a terminal opened before `setx`
still works. For the third-party proxy at 54.236.92.205, FMP_TLS_VERIFY=0 is set for this
process only, and only if FMP_TLS_VERIFY is not set already. The API key is never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

DEFAULT_PORT = 8876
ENV_NAMES = ("FMP_API_KEY", "FMP_BASE_URL", "FMP_PATH_PREFIX", "FMP_AUTH_STYLE", "FMP_TLS_VERIFY",
             "FMP_MAX_PER_MIN", "FMP_TIMEOUT", "FMP_CACHE_DIR")
SKIP_TLS_HOSTS = {"54.236.92.205"}   # third-party FMP proxy with a self-signed certificate
SYMBOL_RE = re.compile(r"^[A-Z0-9.^-]{1,10}$")
HORIZONS = ("swing", "position")
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
STATIC_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
}

_lock = threading.Lock()  # the FMP client's rate limiter and cache are per process: one analysis at a time


# ---------------------------------------------------------------------------- environment
def load_user_env(names=ENV_NAMES) -> list[str]:
    """Fill missing FMP_* variables from the Windows registry (values saved with setx).

    Reads HKCU\\Environment, then the machine environment (setx /M). Returns the names loaded.
    """
    if os.name != "nt":
        return []
    import winreg
    roots = ((winreg.HKEY_CURRENT_USER, r"Environment"),
             (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"))
    loaded = []
    for name in names:
        if os.environ.get(name):
            continue
        for root, subkey in roots:
            try:
                with winreg.OpenKey(root, subkey) as key:
                    value, kind = winreg.QueryValueEx(key, name)
            except OSError:
                continue
            if kind == winreg.REG_EXPAND_SZ:
                value = winreg.ExpandEnvironmentStrings(value)
            os.environ[name] = str(value)
            loaded.append(name)
            break
    return loaded


def apply_proxy_tls_rule() -> bool:
    """Set FMP_TLS_VERIFY=0 for this process when FMP_BASE_URL points at the known proxy host and
    FMP_TLS_VERIFY is unset. Returns True if it was set."""
    base = os.environ.get("FMP_BASE_URL", "").strip()
    host = urllib.parse.urlsplit(base if "://" in base else "https://" + base).hostname or ""
    if host in SKIP_TLS_HOSTS and not os.environ.get("FMP_TLS_VERIFY"):
        os.environ["FMP_TLS_VERIFY"] = "0"
        return True
    return False


def redact(text: str) -> str:
    """Remove the API key (and any apikey=... query value) from a message."""
    key = os.environ.get("FMP_API_KEY", "").strip()
    if key:
        text = text.replace(key, "***")
    return re.sub(r"(?i)(apikey=)[^&\s\"']+", r"\1***", text)


# ---------------------------------------------------------------------------- requests
def static_file(url_path: str) -> Path | None:
    """The file under ui/ that a URL path names, or None (traversal, hidden files, other types)."""
    rel = urllib.parse.unquote(url_path).lstrip("/") or "index.html"
    if any(c in rel for c in ("\\", "\x00", ":")):
        return None
    if any(part in ("", ".", "..") or part.startswith(".") for part in rel.split("/")):
        return None
    path = (HERE / rel).resolve()
    if not path.is_relative_to(HERE) or not path.is_file() or path.suffix.lower() not in STATIC_TYPES:
        return None
    return path


def _error(exc: Exception) -> tuple[int, dict]:
    kinds = {c.__name__ for c in type(exc).__mro__}
    msg = redact(str(exc)) or type(exc).__name__
    if "InvalidKey" in kinds:
        return 500, {"error": f"API key problem: {msg}"}
    if "FMPError" in kinds:
        return 500, {"error": f"FMP error: {msg}"}
    if isinstance(exc, (ValueError, LookupError)):   # e.g. "no price data for XYZ"
        return 400, {"error": msg}
    sys.stderr.write(redact(traceback.format_exc()))
    return 500, {"error": f"{type(exc).__name__}: {msg}"}


def analyze_request(query: dict) -> tuple[int, dict]:
    """Validate the query, run the analysis and return (HTTP status, JSON payload)."""
    symbol = (query.get("symbol") or "").strip().upper()
    if not SYMBOL_RE.match(symbol):
        return 400, {"error": "Invalid symbol: use 1-10 characters A-Z, 0-9, '.', '^' or '-' (e.g. AAPL, BRK.B, ^VIX)."}
    horizon = (query.get("horizon") or "swing").strip().lower()
    if horizon not in HORIZONS:
        return 400, {"error": f"Invalid horizon: use {' or '.join(HORIZONS)}."}
    kwargs = {"refresh": True} if query.get("refresh") == "1" else {}
    with _lock:
        try:
            import analyze
            metrics, chart_data = analyze.run_analysis(symbol, horizon, **kwargs)
        except Exception as exc:  # report it as JSON and keep the server alive
            return _error(exc)
    return 200, {"metrics": metrics, "chart": chart_data}


def to_json(payload: dict) -> bytes:
    from chart import json_safe
    return json.dumps(json_safe(payload, compact=False), ensure_ascii=False, allow_nan=False).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "ta-ui"

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _host_ok(self) -> bool:
        """Only answer requests addressed to this machine (blocks DNS-rebinding pages)."""
        host = (self.headers.get("Host") or "").strip().lower()
        if host.startswith("["):
            host = host[1:].split("]", 1)[0]
        else:
            host = host.rsplit(":", 1)[0]
        return host in LOCAL_HOSTS

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        if not self._host_ok():
            self._send(403, b"Forbidden", "text/plain; charset=utf-8")
            return
        if url.path == "/api/analyze":
            query = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
            try:
                code, payload = analyze_request(query)
                body = to_json(payload)
            except Exception as exc:  # e.g. a payload that cannot be encoded
                code, payload = _error(exc)
                body = json.dumps(payload).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")
            return
        path = static_file(url.path)
        if path is None:
            self._send(404, b"Not found", "text/plain; charset=utf-8")
            return
        self._send(200, path.read_bytes(), STATIC_TYPES[path.suffix.lower()])

    def log_message(self, fmt, *args):
        sys.stderr.write("[ui] " + redact(fmt % args) + "\n")


def make_server(port: int = DEFAULT_PORT, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    return srv


def main(argv=None):
    ap = argparse.ArgumentParser(description="Local UI for the technical-analysis skill")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port on 127.0.0.1 (default {DEFAULT_PORT})")
    ap.add_argument("--no-browser", action="store_true", help="do not open the browser")
    args = ap.parse_args(argv)

    load_user_env()
    if apply_proxy_tls_rule():
        print("Note: HTTPS certificate verification is off for the FMP proxy 54.236.92.205 "
              "(this process only).", file=sys.stderr)
    if not os.environ.get("FMP_API_KEY"):
        print("Warning: FMP_API_KEY is not set; analyses will fail. See SKILL.md > Setup.", file=sys.stderr)

    srv = make_server(args.port)
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"Technical-analysis UI running at {url}  (Ctrl+C to stop)", flush=True)
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
