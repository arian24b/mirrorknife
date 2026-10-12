[English](README.md) | [فارسی](README.fa.md)

<div dir="rtl">

# mirrorknife

یک ابزار خط فرمان تک‌فایلی پایتون، بدون وابستگی بیرونی، که سرورهای DNS و میرورهای بسته‌ها (APT، DNF/YUM، PyPI، npm، رجیستری Docker و چند مورد دیگر) را تست می‌کند و سریع‌ترین گزینه‌ی سالم هر دسته را نشان می‌دهد.

## قابلیت‌ها

- تست هر سرور DNS: زمان پرس‌وجوی UDP، زمان اتصال TCP به پورت 53، زمان پرس‌وجوی TCP و تأخیر `ping` سیستم. اگر پرس‌وجوی UDP یا TCP جواب بدهد، سرور سالم حساب می‌شود.
- تست میرور برای رجیستری Docker، PyPI، npm، Ubuntu/Debian (APT)، خانواده‌ی RHEL (DNF/YUM)، Maven، Gradle، پروکسی Go، NuGet و Composer.
- انتخاب بهترین DNS و بهترین میرور هر دسته، همراه با تنظیمات آماده‌ی کپی (pip، uv، npm، `daemon.json` داکر، Maven، Gradle، GOPROXY، NuGet، Composer و آدرس پایه‌ی APT).
- ابزار رجیستری Docker: `ping`، `catalog` و `tags`.
- رابط curses: `tui` (DNS و میرورها) و `dns-live` (جدول زنده‌ی DNS که در بازه‌ی مشخص تازه می‌شود).
- خروجی جدول، JSON، CSV یا TSV؛ با تکرار تست، آمار p50 و p90 هم می‌دهد.
- هیچ وابستگی بیرونی ندارد.

## پیش‌نیاز و نصب

- پایتون 3.12، 3.13، 3.14 یا 3.15 (روی هر چهار نسخه تست شده).
- دستور `ping` (در لینوکس و macOS هست). `tui` و `dns-live` به `curses` نیاز دارند و روی ویندوز اجرا نمی‌شوند.

mirrorknife یک اسکریپت است، نه بسته‌ی pip. کافی است آن را بگیرید و اجرا کنید:

```bash
git clone https://github.com/arian24b/mirrorknife.git
cd mirrorknife
python3 mirrorknife.py --help
# یا: chmod +x mirrorknife.py && ./mirrorknife.py --help
# یا: uv run mirrorknife.py --help
```

## این ابزار چیست و چطور کار می‌کند

`mirrorknife.py` فهرست IPهای DNS (`DNSs.txt`، هر خط یک IP) و فهرست میرورها (`mirrors_list.yaml`) را می‌خواند و همه را با یک thread pool به‌صورت موازی تست می‌کند (`--jobs`، پیش‌فرض 32).

- DNS: پرس‌وجوی خام `A` را خودش می‌سازد و هم با UDP و هم با TCP می‌فرستد، زمان اتصال TCP به پورت 53 را می‌سنجد و `ping` سیستم را اجرا می‌کند. امتیاز برابر است با `0.6*udp + 0.2*tcp + 0.2*ping` (میلی‌ثانیه؛ کمتر بهتر است و مقدار خالی ۱۰۰۰ حساب می‌شود).
- میرورها: فهرست `packages:` هر ورودی تعیین می‌کند چه تستی اجرا شود. مثلاً `PyPI` مسیر `/simple/` را می‌خواهد، `Docker Registry` مسیر `/v2/` را (کد 200 یا 401 یعنی سالم)، `Ubuntu` و `Debian` دنبال `dists/<suite>/Release` می‌گردند و `Rocky Linux`، `CentOS`، `AlmaLinux` و `EPEL` دنبال `repodata/repomd.xml`. زمان HTTP به DNS، TCP، TLS، اولین بایت و مجموع تقسیم می‌شود.
- بهترین میرور هر دسته: میرور سالمی که کمترین زمان کل را دارد. با `--prefer-geo` میرورهایی که برچسب `geo:` آن‌ها با کلمه‌ی داده‌شده بخواند، جلوتر قرار می‌گیرند.
- فایل YAML را یک تجزیه‌گر ساده‌ی داخلی می‌خواند، نه کتابخانه‌ی YAML. همان ساختار ساده‌ی نمونه‌های پایین را رعایت کنید.

## نحوه‌ی استفاده

گزینه‌های سراسری (`--quick`، `--deep`، `--timeout`، `--retries`، `--jobs`، `--insecure`، `--output`، `--format`، `--only-best`، `--prefer-geo`) باید **قبل از** زیردستور بیایند.

ساخت فایل‌های نمونه (بدون `--force` روی فایل موجود نمی‌نویسد):

```bash
$ python3 mirrorknife.py init
Wrote DNSs.txt and mirrors_list.yaml
```

تست سرورهای DNS و نمایش بهترین:

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

گزینه‌های بیشتر برای DNS:

```bash
python3 mirrorknife.py dns --servers DNSs.txt --domain pypi.org --best
python3 mirrorknife.py dns --servers DNSs.txt --best --export-resolvconf resolv.conf
python3 mirrorknife.py dns --servers DNSs.txt --best --export-hosts hosts.txt
python3 mirrorknife.py --deep dns --servers DNSs.txt --only-best
```

تست میرورها (در این مثال `m.yaml` فقط pypi.org و registry-1.docker.io را دارد):

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

فیلتر دسته، انتخاب suite یا برچسب geo و ذخیره‌ی نتیجه:

```bash
python3 mirrorknife.py mirrors --config mirrors_list.yaml --kinds ubuntu,pypi,docker --best
python3 mirrorknife.py --prefer-geo iran --only-best mirrors --config mirrors_list.yaml --suite jammy
python3 mirrorknife.py --output report.json --format json mirrors --config mirrors_list.yaml
python3 mirrorknife.py --output report.csv --format csv dns --servers DNSs.txt
```

ابزار رجیستری Docker:

```bash
$ python3 mirrorknife.py docker --base https://registry-1.docker.io ping
✅   docker    739ms  https://registry-1.docke…  https://registry-1.docker.io/
$ python3 mirrorknife.py docker --base https://registry-1.docker.io catalog --n 5
Catalog from https://registry-1.docker.io/ -> HTTPError 401 (catalog may be disabled)
python3 mirrorknife.py docker --base https://REGISTRY tags --repo library/alpine
```

رابط‌های تعاملی:

```bash
python3 mirrorknife.py tui --config mirrors_list.yaml --servers DNSs.txt --domain google.com
python3 mirrorknife.py dns-live --servers DNSs.txt --interval 15
```

کلیدهای `tui`: `r` تازه‌سازی، `l` روشن/خاموش کردن تازه‌سازی زنده، `s` حالت مرتب‌سازی، `t` پنل جزئیات، `/` فیلتر، `e` خروجی `mirrorknife_report.json`، `b` بهترین‌ها، `q` یا Esc خروج.

## مرجع دستورها

| دستور | کاربرد | گزینه‌های اصلی |
| --- | --- | --- |
| `init` | ساخت `DNSs.txt` و `mirrors_list.yaml` نمونه | `--dns-file`, `--mirrors-file`, `--force` |
| `dns` | تست DNS و انتخاب بهترین | `--servers`, `--domain` (پیش‌فرض `google.com`), `--limit`, `--best`, `--snippets`, `--json`, `--export-resolvconf`, `--export-hosts` |
| `dns-live` | جدول زنده‌ی DNS (curses) | `--servers`, `--domain`, `--interval` (15) |
| `mirrors` | تست میرورها و انتخاب بهترین هر دسته | `--config`, `--kinds`, `--suite`, `--limit`, `--best`, `--snippets`, `--json`, `--export-hosts` |
| `docker` | `ping` / `catalog` / `tags` رجیستری | `--base` (الزامی), `catalog --n`, `tags --repo` |
| `tui` | رابط تعاملی DNS و میرورها | `--config`, `--servers`, `--domain`, `--kinds`, `--interval` (30) |

پیش‌فرض گزینه‌های سراسری: `--timeout 6`، `--retries 1`، `--jobs 32`. گزینه‌ی `--quick` زمان انتظار را حداکثر 2.5 ثانیه، تکرار را 1 و تعداد کارگرها را حداکثر 16 می‌کند. گزینه‌ی `--deep` زمان انتظار را دست‌کم 10 ثانیه و تکرار را دست‌کم 3 می‌کند. مقادیر `--kinds`: `ubuntu,debian,rhel,pypi,npm,docker,maven,gradle,go,nuget,composer`.

## پیکربندی

فایل DNS: هر خط یک IP؛ خطی که با `#` شروع شود توضیح است.

```text
# One IP per line
1.1.1.1
8.8.8.8
```

فایل میرورها (`name`، `url`، `packages` و در صورت نیاز `geo`/`region`/`country`):

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

مقادیر قابل‌شناسایی `packages:`: `Docker Registry`، `PyPI`، `npm`، `Maven Central`، `Google Maven`، `Jitpack Maven`، `Gradle`، `Go`، `NuGet`، `Composer`، `Ubuntu`، `Debian` و `CentOS`، `Rocky Linux`، `AlmaLinux`، `EPEL`، `Fedora EPEL` (همه خانواده‌ی RHEL حساب می‌شوند). حروف بزرگ و کوچک فرقی ندارند و مقادیر دیگر نادیده گرفته می‌شوند. فایل‌های `DNSs.txt` و `mirrors_list.yaml` موجود در مخزن، فهرست گزینش‌شده‌ای از DNSها و میرورهای ایرانی‌اند.

برای تست APT، suite از `--suite` گرفته می‌شود؛ اگر داده نشود و سیستم لینوکس باشد، از `VERSION_CODENAME` در `/etc/os-release`.

## رفع مشکل

- خطای `unrecognized arguments` برای `--quick`، `--output` و بقیه‌ی گزینه‌های سراسری: آن‌ها را قبل از زیردستور بگذارید.
- `catalog` در Docker کد 401 یا 404 می‌دهد: بسیاری از رجیستری‌ها `/v2/_catalog` را بسته‌اند.
- در شبکه‌ی محدود همه‌چیز خطا می‌دهد: `--timeout` را بالا ببرید یا از `--deep` استفاده کنید.
- صفحه‌هایی در `mirrors_list.yaml` که پورتال هستند و ریشه‌ی مخزن نیستند، ممکن است ناموفق گزارش شوند.

## نکات امنیتی

گزینه‌ی `--insecure` بررسی TLS را خاموش می‌کند؛ فقط برای عیب‌یابی استفاده کنید. ابزار به همه‌ی میزبان‌های فهرست‌ها پرس‌وجوی DNS ساده و درخواست GET روی HTTP(S) می‌فرستد.

## توسعه

```bash
uvx ruff check .
uvx ruff format --check .
for v in 3.12 3.13 3.14 3.15; do uv run --python $v mirrorknife.py --help >/dev/null && echo $v ok; done
```

CI همین بررسی‌ها را روی پایتون 3.12 تا 3.15 اجرا می‌کند.

## مجوز

فایل مجوزی در مخزن وجود ندارد.

</div>
