"""HttpFetch CLI - minimal web fetcher, pure stdlib (urllib).

Commands: get | headers | read | --self-test
Self-test is offline: it validates URL handling against a local data structure,
no network required.
"""
from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse

USAGE = """HttpFetch usage:
  get <url> [--max-bytes N] [--raw]   fetch body; HTML tags stripped unless --raw
  headers <url>                       HEAD request, print response headers
Only http:// and https:// URLs are allowed. 10s timeout."""

TIMEOUT = 10
USER_AGENT = "Vise-HttpFetch/1.0"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\n{3,}")


def validate_url(url: str) -> str | None:
    """Return error message, or None if ok."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"unsupported scheme '{parsed.scheme or '(none)'}' (http/https only)"
    if not parsed.netloc:
        return "missing host"
    return None


def strip_html(text: str) -> str:
    text = _TAG_RE.sub(" ", text)
    lines = [re.sub(r" {2,}", " ", ln).strip() for ln in text.splitlines()]
    text = "\n".join(ln for ln in lines if ln)
    return _WS_RE.sub("\n\n", text)


def _request(url: str, method: str = "GET") -> urllib.request.Request:
    return urllib.request.Request(url, method=method,
                                  headers={"User-Agent": USER_AGENT})


def cmd_get(args: argparse.Namespace) -> int:
    err = validate_url(args.url)
    if err:
        print(f"http_fetch: {err}", file=sys.stderr)
        return 2
    try:
        with urllib.request.urlopen(_request(args.url), timeout=TIMEOUT) as resp:
            raw = resp.read(args.max_bytes)
            charset = resp.headers.get_content_charset() or "utf-8"
    except urllib.error.URLError as e:
        print(f"http_fetch: fetch failed: {e}", file=sys.stderr)
        return 1
    text = raw.decode(charset, errors="replace")
    print(text if args.raw else strip_html(text))
    return 0


def cmd_headers(args: argparse.Namespace) -> int:
    err = validate_url(args.url)
    if err:
        print(f"http_fetch: {err}", file=sys.stderr)
        return 2
    try:
        with urllib.request.urlopen(_request(args.url, "HEAD"), timeout=TIMEOUT) as resp:
            print(f"HTTP {resp.status}")
            for k, v in resp.headers.items():
                print(f"{k}: {v}")
    except urllib.error.URLError as e:
        print(f"http_fetch: fetch failed: {e}", file=sys.stderr)
        return 1
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    if args.topic == "usage":
        print(USAGE)
        return 0
    print(f"http_fetch: unknown topic '{args.topic}' (available: usage)",
          file=sys.stderr)
    return 1


def self_test() -> int:
    # Offline: validate URL gate and HTML stripper, no network.
    assert validate_url("https://example.com") is None
    assert validate_url("ftp://x") is not None
    assert validate_url("not-a-url") is not None
    assert strip_html("<p>Hello <b>world</b></p>") == "Hello world"
    print("http_fetch OK (offline self-test)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="http_fetch")
    parser.add_argument("--self-test", action="store_true")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("get")
    p.add_argument("url")
    p.add_argument("--max-bytes", type=int, default=200000)
    p.add_argument("--raw", action="store_true")

    p = sub.add_parser("headers")
    p.add_argument("url")

    p = sub.add_parser("read")
    p.add_argument("topic")

    args = parser.parse_args()
    if args.self_test:
        return self_test()
    handlers = {"get": cmd_get, "headers": cmd_headers, "read": cmd_read}
    handler = handlers.get(args.command)
    if not handler:
        parser.print_help()
        return 2
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
