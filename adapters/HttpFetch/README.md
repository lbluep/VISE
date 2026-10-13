# HttpFetch

Minimal web fetcher, pure Python stdlib (urllib) — no third-party deps.

## Usage

```bash
visectl run http_fetch get https://example.com --max-bytes 5000
visectl run http_fetch headers https://github.com
visectl run http_fetch get https://example.com --raw   # keep HTML tags
```

Bodies are printed with HTML tags stripped by default (agent-friendly text).
10s timeout; http/https only.
