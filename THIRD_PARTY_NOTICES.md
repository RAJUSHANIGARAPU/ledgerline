# Third-party notices

Ledgerline is MIT-licensed (see LICENSE). It depends on the components below. Versions are those pinned in `uv.lock`. Licences were read from the installed package metadata (`importlib.metadata`) on 2026-11-07 (anthropic, pypdf and their dependencies on 2026-11-08), then corrected from the package's own licence files where the metadata field was empty or a long text. Two Linux-only runtime packages (brotlicffi, tzdata) are not installed on the host used for this check; their entries come from PyPI metadata and are marked unverified.

## Copyleft and weak-copyleft components (read this first)

No GPL or AGPL component is used under a GPL licence. These need a note:

| Component | Licence | Where | Why it is acceptable here |
|---|---|---|---|
| psycopg, psycopg-binary 3.3.8 | LGPL-3.0-only (confirmed in the bundled LICENSE.txt) | runtime | Used as an unmodified library, imported dynamically. LGPL lets an MIT application use it. If you redistribute a container image or bundle, keep this notice and the licence text, and do not modify the library without publishing the changes. The binary wheel also bundles native libraries (libpq and others); their licences are not enumerated here. |
| pycountry 26.2.16 | LGPL-2.1-only | runtime (via schwifty) | Unmodified, imported dynamically. Same conditions. |
| pyphen 0.18.1 | GPL-2.0+ OR LGPL-2.1+ OR MPL-1.1 (tri-licence) | runtime (via WeasyPrint) | Used under the MPL-1.1 / LGPL option, not the GPL option. |
| certifi 2026.10.23 | MPL-2.0 | dev only (via requests, testcontainers) | File-level copyleft, unmodified, not shipped in the runtime image. |
| hypothesis 6.170.15 | MPL-2.0 | dev only | Same. |

System packages in the Docker image (not Python dependencies): Pango, HarfBuzz and FreeType-related libraries (LGPL / MIT, installed from Debian), fonts-dejavu-core (permissive font licence), PostgreSQL client tools (PostgreSQL licence). Debian lists exact licences per package in /usr/share/doc/<package>/copyright inside the image.

## Vendored front-end files (src/ledgerline/static/js)

| File | Component | Version | Licence | How verified |
|---|---|---|---|---|
| htmx.min.js | htmx | 2.0.4 | 0BSD | `license` field of htmx.org@2.0.4 package.json on unpkg; the vendored file carries no header |
| chart.umd.min.js | Chart.js | 4.4.7 | MIT | `license` field of chart.js@4.4.7 package.json on unpkg; file header names chart.js@4.4.7 |

## Runtime dependencies (including transitive)

| Package | Version | Licence |
|---|---|---|
| alembic | 1.20.1 | MIT |
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.8.0 | MIT |
| anthropic | 2.6.0 | Apache-2.0 |
| anyio | 4.16.0 | MIT |
| argon2-cffi | 25.1.0 | MIT |
| argon2-cffi-bindings | 26.1.0 | MIT |
| brotli | 1.2.1 | MIT |
| brotlicffi | 1.2.1.0 | MIT (PyPI metadata; not installed on this host, unverified locally) |
| cffi | 2.1.1 | MIT-0 |
| click | 8.5.1 | BSD-3-Clause |
| cssselect2 | 0.10.1 | BSD-3-Clause |
| defusedxml | 0.7.1 | PSF-2.0 |
| docstring-parser | 0.18.0 | MIT |
| fastapi | 0.162.1 | MIT |
| fonttools | 4.68.0 | MIT |
| h11 | 0.16.0 | MIT |
| httptools | 0.9.0 | MIT |
| httpx2 | 2.16.0 | BSD-3-Clause |
| idna | 3.20 | BSD-3-Clause |
| itsdangerous | 2.2.0 | BSD-3-Clause |
| Jinja2 | 3.1.6 | BSD-3-Clause |
| jiter | 0.19.0 | MIT |
| Mako | 1.4.3 | MIT |
| MarkupSafe | 3.0.4 | BSD-3-Clause |
| mt-940 | 5.1.3 | BSD-3-Clause |
| opentelemetry-api | 1.45.2 | Apache-2.0 |
| pillow | 13.0.0 | MIT-CMU (HPND) |
| psycopg | 3.3.8 | LGPL-3.0-only |
| psycopg-binary | 3.3.8 | LGPL-3.0-only |
| pycountry | 26.2.16 | LGPL-2.1-only |
| pycparser | 3.12 | BSD-3-Clause |
| pydantic | 2.14.3 | MIT |
| pydantic-settings | 2.16.2 | MIT |
| pydantic_core | 2.50.4 | MIT |
| pydyf | 0.13.0 | BSD-3-Clause |
| pypdf | 7.0.0 | BSD-3-Clause |
| pyphen | 0.18.1 | GPL-2.0+ OR LGPL-2.1+ OR MPL-1.1 (used under MPL-1.1 / LGPL option) |
| python-dotenv | 1.2.4 | BSD-3-Clause |
| python-multipart | 0.0.33 | Apache-2.0 |
| PyYAML | 6.0.3 | MIT |
| rstr | 3.2.2 | BSD-3-Clause |
| schwifty | 2026.11.0 | MIT |
| sniffio | 1.3.1 | MIT OR Apache-2.0 |
| SQLAlchemy | 2.1.8 | MIT |
| starlette | 1.10.0 | BSD-3-Clause |
| tinycss2 | 1.5.1 | BSD-3-Clause |
| tinyhtml5 | 2.1.0 | MIT |
| typing-inspection | 0.4.5 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| tzdata | 2026.6 | Apache-2.0 (PyPI metadata; not installed on this host, unverified locally) |
| uvicorn | 0.54.0 | BSD-3-Clause |
| uvloop | 0.23.0 | MIT OR Apache-2.0 |
| watchfiles | 1.3.0 | MIT |
| weasyprint | 70.0 | BSD-3-Clause |
| webencodings | 0.6.1 | BSD-3-Clause |
| websockets | 17.4 | BSD-3-Clause |
| zopfli | 0.4.3 | Apache-2.0 |

## Development and test dependencies (not shipped in the runtime image)

| Package | Version | Licence |
|---|---|---|
| certifi | 2026.10.23 | MPL-2.0 |
| charset-normalizer | 3.5.2 | MIT |
| coverage | 7.17.2 | Apache-2.0 |
| docker | 7.2.0 | Apache-2.0 |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpx | 0.28.1 | BSD-3-Clause |
| hypothesis | 6.170.15 | MPL-2.0 |
| iniconfig | 2.3.1 | MIT |
| libcst | 1.9.0 | MIT (plus PSF-licensed parts) |
| linkify-it-py | 2.2.0 | MIT |
| markdown-it-py | 4.2.0 | MIT |
| mdit-py-plugins | 0.6.1 | MIT |
| mdurl | 0.1.2 | MIT |
| mutmut | 3.8.1 | BSD-3-Clause |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| platformdirs | 4.14.5 | MIT |
| pluggy | 1.7.0 | MIT |
| Pygments | 2.21.0 | BSD-2-Clause |
| pytest | 9.2.0 | MIT |
| PyYAML-ft | 8.0.0 | MIT |
| requests | 2.34.2 | Apache-2.0 |
| rich | 15.0.0 | MIT |
| ruff | 0.17.4 | MIT |
| setproctitle | 1.3.8 | BSD-3-Clause |
| sortedcontainers | 2.4.0 | Apache-2.0 |
| testcontainers | 4.15.0 | Apache-2.0 |
| textual | 8.2.8 | MIT |
| urllib3 | 2.8.0 | MIT |
| wrapt | 2.5.2 | BSD-2-Clause |
