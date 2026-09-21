"""Build the browser-only demo into site/: index.html plus a README.md for a free Hugging Face Static Space.

    python scripts/build_static.py --author "Your Name" --repo-url https://github.com/<you>/reconloop

The page loads Pyodide (CPython compiled to WebAssembly) from jsDelivr and runs this package in the
visitor's browser. The package, the sample batch and the published results are embedded in the page.
"""

from __future__ import annotations

import argparse
import base64
import html
import io
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reconloop import service, web  # noqa: E402

PYODIDE_VERSION = "314.0.7"
TEMPLATE = ROOT / "static" / "index.template.html"

SPACE_README = """---
title: ReconLoop
emoji: 📒
colorFrom: indigo
colorTo: gray
sdk: static
app_file: index.html
license: mit
short_description: Settlement reconciliation agent graded on held-out data
---

# ReconLoop

A Razorpay settlement reconciliation agent that runs entirely in your browser (Python via Pyodide) and is
scored against an answer key on every run.{repo_line}
"""


def bundle() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted((ROOT / "reconloop").rglob("*.py")):
            zf.write(f, f.relative_to(ROOT).as_posix())
        zf.write(ROOT / "bench" / "results.md", "bench/results.md")
        for f in sorted((ROOT / "data" / "sample").iterdir()):
            if f.is_file():
                zf.write(f, f"data/sample/{f.name}")
    return buf.getvalue()


def build(out: Path, author: str | None = None, repo_url: str | None = None) -> Path:
    by = f"Built by {html.escape(author)} with Claude as a pair programmer." if author else \
        "Built with Claude as a pair programmer."
    src = f' Source: <a href="{html.escape(repo_url, quote=True)}">{html.escape(repo_url)}</a>.' if repo_url else ""
    footer = f"{by}{src} Synthetic data throughout; nothing on this page moves money."
    page = TEMPLATE.read_text(encoding="utf-8")
    for key, value in {
        "{{PYODIDE_VERSION}}": PYODIDE_VERSION,
        "{{BUNDLE_B64}}": base64.b64encode(bundle()).decode("ascii"),
        "{{PUBLISHED_HTML}}": web.md_to_html(service.published_results()),
        "{{HOW_HTML}}": web.md_to_html(service.HOW_MD),
        "{{FOOTER_HTML}}": footer,
    }.items():
        if key not in page:
            raise SystemExit(f"template is missing {key}")
        page = page.replace(key, value)
    if "{{" in page:
        raise SystemExit("unfilled placeholder left in the page")
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(page, encoding="utf-8")
    repo_line = f"\n\nSource and benchmark: {repo_url}" if repo_url else ""
    (out / "README.md").write_text(SPACE_README.format(repo_line=repo_line), encoding="utf-8")
    return out / "index.html"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=ROOT / "site")
    ap.add_argument("--author")
    ap.add_argument("--repo-url")
    args = ap.parse_args(argv)
    page = build(args.out, args.author, args.repo_url)
    print(f"Wrote {page} ({page.stat().st_size / 1024:.0f} KB) and {args.out / 'README.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
