# mirrorknife 🧰 (DNS + Mirrors Swiss-Knife)

A stdlib-only Python CLI that helps you **test DNS servers** and **pick the best local mirrors** (Ubuntu/Debian/RHEL-family, PyPI, npm, Docker Registry).
Works on **macOS**, **Ubuntu/Linux**, and **Raspberry Pi**.

---

## Features (English)

### DNS tool
- Checks DNS servers from a file (one IP per line)
- Measures:
  - UDP DNS query time (A record)
  - TCP connect time to port 53
  - TCP DNS query time
  - Ping average latency (system ping)
- Considers DNS **healthy** if UDP or TCP resolves successfully
- Picks **best DNS** using a weighted score of UDP/TCP/Ping

### Mirrors tool
Reads `mirrors_list.yaml` and checks (depending on `packages:` tags):
- **Docker Registry**: health check `GET /v2/` (200 or 401 is considered healthy)
- **PyPI**: checks `GET /simple/`
- **npm**: tries `/-/ping`, `/~/ping`, then `/`
- **Ubuntu/Debian (APT)**: checks `dists/<suite>/Release` with smart subpath probing (`/`, `/ubuntu/`, `/debian/`)
- **RHEL-family (DNF/YUM)**: checks `repodata/repomd.xml` using multiple common paths
- **Maven/Gradle**: checks common repository roots
- **Go modules**: probes module proxy paths
- **NuGet**: checks `v3/index.json`
- **Composer**: checks `packages.json`

### Docker tool
- `ping`: check `/v2/`
- `catalog`: list repositories from `/v2/_catalog` *(often disabled by registries)*
- `tags`: list tags for a known repo via `/v2/<repo>/tags/list`

### Init tool
- `init`: generate sample `DNSs.txt` and `mirrors_list.yaml`

### TUI (optional)
Interactive curses UI:
- refresh, filter, export JSON, show best picks, live refresh

---

## Requirements
- Python **3.9+**
- `ping` command available in OS (macOS/Linux have it)
- No Python dependencies (stdlib only)

---

## Install / Setup
1. Save the script as `mirrorknife.py`
2. Make it executable:

```bash
chmod +x mirrorknife.py
````

3. Create config files (samples below)

---

## Sample config files

### 1) DNS servers file: `dns_iran.txt`

```text
# One IP per line
78.157.42.100
185.51.200.2
10.202.10.202
8.8.8.8
1.1.1.1
```

### 2) Mirrors config: `mirrors_list.yaml`

> Your tool uses a YAML-lite parser. Keep the structure simple:

* top-level `mirrors:`
* each item has `name`, `url`, and `packages` list

```yaml
mirrors:
  - name: Shatel
    url: https://mirror.shatel.ir/
    packages:
      - Ubuntu
      - Debian

  - name: IUT
    url: https://repo.iut.ac.ir/
    packages:
      - Ubuntu
      - Debian
      - Rocky Linux
      - Fedora

  - name: Runflare PyPI
    url: https://mirror-pypi.runflare.com/
    packages:
      - PyPI

  - name: HamDocker Registry
    url: https://hub.hamdocker.ir/
    packages:
      - Docker Registry

  - name: PardisCo (multi)
    url: https://mirrors.pardisco.co/
    packages:
      - Ubuntu
      - Debian
      - PyPI
      - npm
      - Docker Registry
```

---

## Usage (English)

### Global options

* `--quick` quick preset (lower timeout, fewer retries)
* `--deep` deep preset (higher timeout, more retries)
* `--retries N` number of attempts per target
* `--only-best` skip table, print best picks only
* `--output FILE` + `--format json|csv|tsv` write results to file
* `--prefer-geo TAG` prefer mirrors matching `geo` tag/keyword

### 1) Check DNS servers + best DNS

```bash
./mirrorknife.py dns --servers dns_iran.txt --domain pypi.org --best
```

Export DNS snippets:

```bash
./mirrorknife.py dns --servers dns_iran.txt --best --export-resolvconf resolv.conf
./mirrorknife.py dns --servers dns_iran.txt --best --export-hosts hosts.txt
```

Quick modes:

```bash
./mirrorknife.py dns --servers dns_iran.txt --quick --only-best
./mirrorknife.py dns --servers dns_iran.txt --deep --retries 3
```

### 2) Check mirrors + best per type

```bash
./mirrorknife.py mirrors --config mirrors_list.yaml --best
```

Export best mirror hosts template:

```bash
./mirrorknife.py mirrors --config mirrors_list.yaml --best --export-hosts hosts.txt
```

Prefer local suite + geo tag:

```bash
./mirrorknife.py mirrors --config mirrors_list.yaml --suite jammy --prefer-geo iran --only-best
```

### 3) Only check some kinds

```bash
./mirrorknife.py mirrors --config mirrors_list.yaml --kinds ubuntu,pypi,docker --best
```

### 3b) Save output to a file

```bash
./mirrorknife.py mirrors --config mirrors_list.yaml --output report.json --format json
./mirrorknife.py mirrors --config mirrors_list.yaml --output report.csv --format csv
```

### 4) Docker registry tools

```bash
# Health check
./mirrorknife.py docker --base https://hub.hamdocker.ir ping

# Catalog (if enabled; many registries disable this)
./mirrorknife.py docker --base https://hub.hamdocker.ir catalog --n 50

# Tags for a known repo
./mirrorknife.py docker --base https://hub.hamdocker.ir tags --repo library/alpine
```

### 5) Interactive TUI

```bash
./mirrorknife.py tui --config mirrors_list.yaml --servers dns_iran.txt --domain google.com
```

### 6) Init sample files

```bash
./mirrorknife.py init --dns-file DNSs.txt --mirrors-file mirrors_list.yaml
```

Overwrite existing files:

```bash
./mirrorknife.py init --dns-file DNSs.txt --mirrors-file mirrors_list.yaml --force
```

**TUI keys**

* `r` refresh
* `l` live refresh toggle
* `s` sort mode (latency/ok/kind)
* `t` toggle details panel
* `/` filter
* `e` export `mirrorknife_report.json`
* `b` show best picks
* `q` quit

---

## Output & How “Best” is chosen

* DNS “best” = successful DNS answer + lowest weighted score of UDP/TCP/Ping (UDP preferred)
* Mirror “best” = successful probe + lowest `total_ms`
* If something fails, it shows `BAD` and includes a note like timeout/DNS failure/HTTP code.
* With retries (`--retries`), outputs include `p50_ms`/`p90_ms` stats.

---

## Quick config snippets (you can copy/paste)

### pip (PyPI mirror)

```bash
pip config set global.index-url https://YOUR_PYPI_MIRROR/simple
```

### uv (PyPI mirror)

**Option A: environment variables**

```bash
export UV_INDEX="https://YOUR_PYPI_MIRROR/simple"
# or add extra indexes:
export UV_EXTRA_INDEX_URL="https://YOUR_PYPI_MIRROR/simple"
```

**Option B: pyproject.toml**

```toml
[[tool.uv.index]]
name = "local"
url = "https://YOUR_PYPI_MIRROR/simple"
default = true
```

### npm (registry/mirror)

```bash
npm config set registry https://YOUR_NPM_MIRROR/
```

### Docker (registry mirror)

Edit `/etc/docker/daemon.json` (Linux) or Docker Desktop settings (macOS):

```json
{
  "registry-mirrors": ["https://YOUR_DOCKER_MIRROR"]
}
```

---

## Troubleshooting

* **TLS errors / corporate MITM / custom certs**: try `--insecure` (not recommended long-term).
* **Docker catalog empty / fails**: many registries disable `/v2/_catalog` or require auth. Use `tags` for known repos instead.
* **Some mirror URLs are “portal pages”**: those may return 200 but are not repo roots. Prefer direct mirror roots for best results.
* **npm checks fail**: some mirrors are not a full npm registry. Update the `url:` to the actual registry endpoint (often includes a subpath like `/npm/`).

## YAML-lite rules (mirrors_list.yaml)

* Each mirror entry must include `name`, `url`, and `packages`.
* `packages` must be a list of `- Item` lines indented under `packages:`.
* Other keys (like `description`) are allowed and ignored by the parser.
* Optional `geo`, `region`, or `country` fields can be used with `--prefer-geo`.

---

# راهنما (فارسی)

`mirrorknife` یک ابزار خط فرمان پایتون (بدون وابستگی خارجی) است برای:

* تست و انتخاب **بهترین DNS**
* تست و انتخاب **بهترین Mirror** برای:

  * اوبونتو/دبیان (APT)
  * خانواده ردهت (DNF/YUM)
  * PyPI (pip/uv)
  * npm
  * Docker Registry

روی **macOS**، **لینوکس/اوبونتو** و **Raspberry Pi** اجرا می‌شود.

---

## قابلیت‌ها (فارسی)

### ابزار DNS

* فایل DNS (هر خط یک IP) را می‌خواند
* اندازه‌گیری‌ها:

  * زمان Query روی UDP
  * زمان اتصال TCP به پورت ۵۳
  * زمان Query روی TCP
  * میانگین ping
* انتخاب بهترین DNS بر اساس امتیاز ترکیبی UDP/TCP/Ping

### ابزار Mirrors

فایل `mirrors_list.yaml` را می‌خواند و بر اساس `packages:` تست مناسب انجام می‌دهد:

* **Docker Registry**: تست `/v2/` (کد 200 یا 401 سالم محسوب می‌شود)
* **PyPI**: تست `/simple/`
* **npm**: تست `/-/ping` و `/~/ping` و در نهایت `/`
* **Ubuntu/Debian**: تست `dists/<suite>/Release` (با حدس مسیرهای رایج)
* **RHEL-family**: تست `repodata/repomd.xml` با مسیرهای رایج
* **Maven/Gradle**: تست روت‌های رایج ریپازیتوری
* **Go modules**: تست مسیرهای proxy
* **NuGet**: تست `v3/index.json`
* **Composer**: تست `packages.json`

### ابزار Docker

* `ping`: تست `/v2/`
* `catalog`: لیست ریپوها از `/v2/_catalog` (خیلی وقت‌ها غیرفعال است)
* `tags`: لیست تگ‌های یک ریپو مشخص

### ابزار Init

* `init`: ساخت نمونه فایل‌های `DNSs.txt` و `mirrors_list.yaml`

### TUI (اختیاری)

محیط تعاملی با `curses`:

* refresh، فیلتر، خروجی JSON، نمایش بهترین‌ها

---

## پیش‌نیازها

* Python **3.9+**
* دستور `ping` روی سیستم
* بدون هیچ پکیج اضافی (فقط stdlib)

---

## نصب و اجرا

1. فایل را با نام `mirrorknife.py` ذخیره کنید
2. قابل اجرا کنید:

```bash
chmod +x mirrorknife.py
```

---

## نمونه فایل‌ها

### 1) فایل DNS: `dns_iran.txt`

```text
# هر خط یک IP
78.157.42.100
185.51.200.2
10.202.10.202
8.8.8.8
1.1.1.1
```

### 2) فایل Mirror ها: `mirrors_list.yaml`

```yaml
mirrors:
  - name: Shatel
    url: https://mirror.shatel.ir/
    packages:
      - Ubuntu
      - Debian

  - name: Runflare PyPI
    url: https://mirror-pypi.runflare.com/
    packages:
      - PyPI

  - name: HamDocker Registry
    url: https://hub.hamdocker.ir/
    packages:
      - Docker Registry
```

---

## دستورات (فارسی)

### گزینه‌های سراسری

* `--quick` حالت سریع
* `--deep` حالت دقیق با زمان بیشتر
* `--retries N` تعداد تلاش برای هر هدف
* `--only-best` فقط بهترین‌ها را چاپ کن
* `--output FILE` + `--format json|csv|tsv` ذخیره خروجی
* `--prefer-geo TAG` اولویت به geo/region

### تست DNS و انتخاب بهترین

```bash
./mirrorknife.py dns --servers dns_iran.txt --domain pypi.org --best
```

خروجی فایل نمونه DNS:

```bash
./mirrorknife.py dns --servers dns_iran.txt --best --export-resolvconf resolv.conf
./mirrorknife.py dns --servers dns_iran.txt --best --export-hosts hosts.txt
```

### تست Mirror ها و انتخاب بهترین‌ها

```bash
./mirrorknife.py mirrors --config mirrors_list.yaml --best
```

خروجی فایل hosts برای بهترین Mirrorها:

```bash
./mirrorknife.py mirrors --config mirrors_list.yaml --best --export-hosts hosts.txt
```

### تست فقط بعضی دسته‌ها

```bash
./mirrorknife.py mirrors --config mirrors_list.yaml --kinds ubuntu,pypi,docker --best
```

### ذخیره خروجی در فایل

```bash
./mirrorknife.py mirrors --config mirrors_list.yaml --output report.json --format json
./mirrorknife.py mirrors --config mirrors_list.yaml --output report.csv --format csv
```

### ابزار Docker

```bash
./mirrorknife.py docker --base https://hub.hamdocker.ir ping
./mirrorknife.py docker --base https://hub.hamdocker.ir catalog --n 50
./mirrorknife.py docker --base https://hub.hamdocker.ir tags --repo library/alpine
```

### محیط تعاملی

```bash
./mirrorknife.py tui --config mirrors_list.yaml --servers dns_iran.txt --domain google.com
```

### ساخت فایل‌های نمونه

```bash
./mirrorknife.py init --dns-file DNSs.txt --mirrors-file mirrors_list.yaml
```

بازنویسی فایل‌های موجود:

```bash
./mirrorknife.py init --dns-file DNSs.txt --mirrors-file mirrors_list.yaml --force
```

کلیدها:

* `r` بروزرسانی
* `l` روشن/خاموش کردن refresh خودکار
* `s` تغییر ترتیب (latency/ok/kind)
* `t` نمایش/عدم نمایش پنل جزئیات
* `/` فیلتر
* `e` خروجی `mirrorknife_report.json`
* `b` نمایش بهترین‌ها
* `q` خروج

---

## نکات رفع مشکل

* خطای SSL/TLS: از `--insecure` استفاده کنید (موقت و غیر پیشنهادی)
* `catalog` در Docker کار نکرد: طبیعی است؛ خیلی از رجیستری‌ها این قابلیت را خاموش می‌کنند یا نیاز به احراز هویت دارند
* بعضی URLها صفحه معرفی هستند نه روت ریپو: بهتر است `url:` را روی روت واقعی مخزن تنظیم کنید
* npm: اگر `/ping` جواب نمی‌دهد، احتمالاً URL شما روت Registry واقعی نیست و نیاز به subpath دارد

## قوانین YAML-lite (mirrors_list.yaml)

* هر آیتم باید `name`، `url` و `packages` داشته باشد
* زیر `packages` باید لیست `- Item` با تورفتگی مناسب بیاید
* کلیدهای دیگر مثل `description` مجاز هستند و توسط parser نادیده گرفته می‌شوند
* فیلدهای `geo` یا `region` یا `country` برای `--prefer-geo` قابل استفاده هستند

---

## References

* Docker Registry auth challenge & `/v2/` behavior: [https://docs.docker.com/reference/api/registry/auth/](https://docs.docker.com/reference/api/registry/auth/)
* Docker Registry HTTP API V2: [https://docker-docs.uclv.cu/registry/spec/api/](https://docker-docs.uclv.cu/registry/spec/api/)
* PyPI Simple API: [https://packaging.python.org/en/latest/guides/hosting-your-own-index/](https://packaging.python.org/en/latest/guides/hosting-your-own-index/)
* pip configuration: [https://pip.pypa.io/en/stable/topics/configuration/](https://pip.pypa.io/en/stable/topics/configuration/)
* uv indexes & env vars: [https://docs.astral.sh/uv/concepts/indexes/](https://docs.astral.sh/uv/concepts/indexes/)  and  [https://docs.astral.sh/uv/reference/environment/](https://docs.astral.sh/uv/reference/environment/)


If you want, I can also generate a **ready-to-copy folder layout** (script + configs + this README) as a zip-like “tree” so you can just drop it into a repo.
::contentReference[oaicite:3]{index=3}
```

[1]: https://docs.docker.com/reference/api/registry/auth/?utm_source=chatgpt.com "Registry authentication | Docker Docs"
[2]: https://packaging.python.org/en/latest/guides/hosting-your-own-index/?utm_source=chatgpt.com "Hosting your own simple repository - Python Packaging User Guide"
[3]: https://docs.astral.sh/uv/concepts/indexes/?utm_source=chatgpt.com "Package indexes | uv"
