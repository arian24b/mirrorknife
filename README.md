[English](README.md) | [فارسی](README.fa.md)

# mirrorknife

A single-file, stdlib-only Python CLI that tests DNS servers and package mirrors (APT, DNF/YUM, PyPI, npm, Docker registries and more) and prints the fastest healthy one for each kind.

## Features

- DNS check per server: UDP query time, TCP connect time to port 53, TCP query time and system `ping` latency. A server is healthy if the UDP or TCP query resolves.
- Mirror checks for Docker registry, PyPI, npm, Ubuntu/Debian (APT), RHEL-family (DNF/YUM), Maven, Gradle, Go proxy, NuGet and Composer.
- Picks the best DNS server and the best mirror per kind, and prints ready-to-paste config snippets (pip, uv, npm, Docker `daemon.json`, Maven, Gradle, GOPROXY, NuGet, Composer, APT base URL).
- Docker registry helper: `ping`, `catalog`, `tags`.
- Curses UIs: `tui` (DNS + mirrors) and `dns-live` (DNS table that refreshes on an interval).
- Output as table, JSON, CSV or TSV; retries with p50/p90 stats.
- No third-party dependencies.

## Requirements and installation

- Python 3.12, 3.13, 3.14 or 3.15 (tested on all four).
- The `ping` command (Linux and macOS ship it). `tui` and `dns-live` need `curses`, so they do not run on Windows.

mirrorknife is one script, not a package. Download it and run it:

```bash
git clone https://github.com/arian24b/mirrorknife.git
cd mirrorknife
python3 mirrorknife.py --help
# or: chmod +x mirrorknife.py && ./mirrorknife.py --help
# or: uv run mirrorknife.py --help
```

## What it is and how it works

`mirrorknife.py` reads a list of DNS server IPs (`DNSs.txt`, one per line) and a mirror list (`mirrors_list.yaml`) and probes everything in parallel with a thread pool (`--jobs`, default 32).

- DNS: it builds a raw DNS `A` query by hand and sends it over UDP and over TCP, measures TCP connect time to port 53, and runs the system `ping`. The score is `0.6*udp + 0.2*tcp + 0.2*ping` (ms, lower is better; a missing value counts as 1000).
- Mirrors: each entry's `packages:` list decides which probes run. For example `PyPI` requests `/simple/`, `Docker Registry` requests `/v2/` (200 or 401 is healthy), `Ubuntu`/`Debian` look for `dists/<suite>/Release`, and `Rocky Linux`/`CentOS`/`AlmaLinux`/`EPEL` look for `repodata/repomd.xml`. HTTP timing is split into DNS, TCP, TLS, time to first byte and total.
- Best mirror per kind: the healthy one with the lowest total time. `--prefer-geo` ranks mirrors whose `geo:` tag matches the keyword first.
- The YAML file is read by a small built-in parser, not a YAML library. Keep to the simple shape shown below.

## Usage

Global options (`--quick`, `--deep`, `--timeout`, `--retries`, `--jobs`, `--insecure`, `--output`, `--format`, `--only-best`, `--prefer-geo`) go **before** the subcommand.

Create sample config files (refuses to overwrite without `--force`):

```bash
$ python3 mirrorknife.py init
Wrote DNSs.txt and mirrors_list.yaml
```

Check DNS servers and show the best one:

```bash
$ python3 mirrorknife.py --quick dns --servers d.txt --best
OK  KIND        LAT  NAME                      BASE/ADDR
------------------------------------------------------------------------------------------
✅   dns       130ms  1.1.1.1                   1.1.1.1
✅   dns       130ms  8.8.8.8                   8.8.8.8

=== BEST PICKS ===
DNS best for google.com: 1.1.1.1  (udp_dns=130ms, ping=2ms)
...
```

More DNS options:

```bash
python3 mirrorknife.py dns --servers DNSs.txt --domain pypi.org --best
python3 mirrorknife.py dns --servers DNSs.txt --best --export-resolvconf resolv.conf
python3 mirrorknife.py dns --servers DNSs.txt --best --export-hosts hosts.txt
python3 mirrorknife.py --deep dns --servers DNSs.txt --only-best
```

Check mirrors (`m.yaml` here lists pypi.org and registry-1.docker.io):

```bash
$ python3 mirrorknife.py --quick mirrors --config m.yaml --best
OK  KIND        LAT  NAME                      BASE/ADDR
------------------------------------------------------------------------------------------
✅   pypi     1066ms  PyPI                      https://pypi.org/
✅   docker   1143ms  Docker Hub                https://registry-1.docker.io/

=== BEST PICKS ===
  pypi: PyPI  base=https://pypi.org/  total=1066ms
...
# pip (index url)
pip config set global.index-url https://pypi.org/simple
```

Filter, prefer a suite or geo tag, and save results:

```bash
python3 mirrorknife.py mirrors --config mirrors_list.yaml --kinds ubuntu,pypi,docker --best
python3 mirrorknife.py --prefer-geo iran --only-best mirrors --config mirrors_list.yaml --suite jammy
python3 mirrorknife.py --output report.json --format json mirrors --config mirrors_list.yaml
python3 mirrorknife.py --output report.csv --format csv dns --servers DNSs.txt
```

Docker registry tools:

```bash
$ python3 mirrorknife.py docker --base https://registry-1.docker.io ping
✅   docker    739ms  https://registry-1.docke…  https://registry-1.docker.io/
$ python3 mirrorknife.py docker --base https://registry-1.docker.io catalog --n 5
Catalog from https://registry-1.docker.io/ -> HTTPError 401 (catalog may be disabled)
python3 mirrorknife.py docker --base https://REGISTRY tags --repo library/alpine
```

Interactive UIs:

```bash
python3 mirrorknife.py tui --config mirrors_list.yaml --servers DNSs.txt --domain google.com
python3 mirrorknife.py dns-live --servers DNSs.txt --interval 15
```

`tui` keys: `r` refresh, `l` toggle live refresh, `s` sort mode, `t` details panel, `/` filter, `e` export `mirrorknife_report.json`, `b` best picks, `q` or Esc quit.

## Commands reference

| Command | Purpose | Main options |
| --- | --- | --- |
| `init` | write sample `DNSs.txt` and `mirrors_list.yaml` | `--dns-file`, `--mirrors-file`, `--force` |
| `dns` | check DNS servers, pick best | `--servers`, `--domain` (default `google.com`), `--limit`, `--best`, `--snippets`, `--json`, `--export-resolvconf`, `--export-hosts` |
| `dns-live` | live DNS table (curses) | `--servers`, `--domain`, `--interval` (15) |
| `mirrors` | check mirrors, pick best per kind | `--config`, `--kinds`, `--suite`, `--limit`, `--best`, `--snippets`, `--json`, `--export-hosts` |
| `docker` | registry `ping` / `catalog` / `tags` | `--base` (required), `catalog --n`, `tags --repo` |
| `tui` | interactive UI over DNS and mirrors | `--config`, `--servers`, `--domain`, `--kinds`, `--interval` (30) |

Global defaults: `--timeout 6`, `--retries 1`, `--jobs 32`. `--quick` caps timeout at 2.5 s, 1 retry, 16 jobs. `--deep` raises timeout to at least 10 s and retries to at least 3. `--kinds` accepts `ubuntu,debian,rhel,pypi,npm,docker,maven,gradle,go,nuget,composer`.

## Configuration

DNS file: one IP per line, `#` starts a comment.

```text
# One IP per line
1.1.1.1
8.8.8.8
```

Mirror file (`name`, `url`, `packages`, optional `geo`/`region`/`country`):

```yaml
mirrors:
  - name: Runflare PyPI
    url: https://mirror-pypi.runflare.com/
    geo: iran
    packages:
      - PyPI
  - name: HamDocker Registry
    url: https://hub.hamdocker.ir/
    packages:
      - Docker Registry
```

Recognised `packages:` values: `Docker Registry`, `PyPI`, `npm`, `Maven Central`, `Google Maven`, `Jitpack Maven`, `Gradle`, `Go`, `NuGet`, `Composer`, `Ubuntu`, `Debian`, and `CentOS`, `Rocky Linux`, `AlmaLinux`, `EPEL`, `Fedora EPEL` (all treated as RHEL-family). Matching is case-insensitive; other values are ignored. The repository's `DNSs.txt` and `mirrors_list.yaml` are a curated list of Iranian DNS servers and mirrors.

For APT probes the suite comes from `--suite` or, on Linux, from `VERSION_CODENAME` in `/etc/os-release`.

## Troubleshooting

- `error: unrecognized arguments` for `--quick`, `--output` and the other global options: move them before the subcommand.
- Docker `catalog` returns 401 or 404: many registries disable `/v2/_catalog`.
- Everything fails in a restricted network: raise `--timeout`, or use `--deep`.
- Portal pages listed as mirrors in `mirrors_list.yaml` may be reported as failed because they are not repository roots.

## Security notes

`--insecure` turns off TLS verification; use it only for debugging. The tool sends plain DNS queries and HTTP(S) GET requests to every host in your lists.

## Development

```bash
uvx ruff check .
uvx ruff format --check .
for v in 3.12 3.13 3.14 3.15; do uv run --python $v mirrorknife.py --help >/dev/null && echo $v ok; done
```

CI runs the same checks on Python 3.12 to 3.15.

## License

No license file is present in the repository.
