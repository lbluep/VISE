# githubToolkit

GitHub connectivity toolkit adapter for Vise. Installs one global command,
**`congit`**, that fixes the common "github.com times out while DNS looks
fine" problem: it discovers alive GitHub IPs from multiple DNS-over-HTTPS
providers, probes each one with a real HTTPS download, and pins the fastest
verified IPs in the hosts file.

## Command

After `viseinit` (or `visectl thread github_toolkit`), `congit` is
installed to `~/.local/bin` and works from any opened terminal:

```bash
congit                 # probe + update /etc/hosts with verified IPs
congit --dry-run       # preview the block without changing anything
congit --restore       # remove the managed block (revert to plain DNS)
congit --self-check    # instant sanity check, no network / no sudo
congit --help          # usage
```

Re-running `congit` is safe — the managed block
(`# >>> github-hosts-fix >>>`) is replaced, never duplicated, and a
timestamped hosts backup is created every time.

## How it works

1. Resolve each GitHub domain via AliDNS / DNSPod / Cloudflare / Google
   DoH, then system DNS fallback.
2. Probe up to 8 candidate IPs per domain **in parallel** — a complete
   small-file download must finish within 6s, so throttled IPs that answer
   headers then stall are rejected.
3. Keep the fastest verified IP per domain and atomically replace the
   managed block in the hosts file (Linux/macOS/Git Bash).
4. Flush DNS cache and verify `https://github.com` is fully reachable.

Domains with no alive IP (e.g. SNI-level blocked hosts) are skipped.

## Files

| File | Role |
| --- | --- |
| `vise.adapter.json` | Adapter contract: requires `curl`, thread init, healthcheck |
| `bin/congit` | The self-contained bash implementation (bash + curl only) |
| `thread` | Bootstrap: installs `~/.local/bin/congit` wrapper + writes state |
| `src/github_toolkit/main.py` | Thin Python shim so `visectl run github_toolkit` works |
| `vise_extensions.sh` | Optional namespaced function `vise_github_toolkit_congit` |

## Vise integration

```bash
visectl thread github_toolkit       # install / refresh the congit command
visectl run github_toolkit --dry-run
visectl doctor                      # verify dependency + health
```
