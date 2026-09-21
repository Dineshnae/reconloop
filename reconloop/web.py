"""Entry points for the in-browser demo, which runs this package under Pyodide.

Every function returns a JSON string, so the page's JavaScript never touches
Python objects. The work itself is done by `service`, the same layer behind
the Gradio app, so both demos run identical engine code.
"""

from __future__ import annotations

import base64
import html
import json
import re
from typing import Any

from . import service
from .bench import parse_seeds

BROWSER_TIE_BREAKERS = ("Rules only", "Greedy baseline", "Oracle ceiling")
BROWSER_MAX_SEEDS = 10          # the browser runs on the page's main thread; keep it snappy


# ------------------------------------------------------------------ markdown
def _inline(text: str) -> str:
    s = html.escape(text, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
    return s


def md_to_html(md: str) -> str:
    """Just enough Markdown for the summaries and tables this package writes:
    headings, pipe tables, bullet lists, paragraphs, bold and inline code."""
    out: list[str] = []
    rows: list[list[str]] = []
    bullets: list[str] = []
    kind = ["ul"]                                           # list type of the open list
    para: list[str] = []

    def flush() -> None:
        if rows:
            head, body = rows[0], rows[1:]      # first row is the header, or an empty placeholder
            thead = ("<thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr></thead>"
                     if any(head) else "")
            trs = "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in body)
            out.append(f'<div class="wide"><table>{thead}<tbody>{trs}</tbody></table></div>')
            rows.clear()
        if bullets:
            out.append(f"<{kind[0]}>" + "".join(f"<li>{_inline(b)}</li>" for b in bullets) + f"</{kind[0]}>")
            bullets.clear()
        if para:
            out.append(f"<p>{_inline(' '.join(para))}</p>")
            para.clear()

    for raw in md.splitlines():
        line = raw.strip()
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(c and set(c) <= set("-: ") for c in cells):
                continue                                    # |---|---| separator
            if not rows:
                flush()                                     # close an open paragraph or list
            rows.append(cells)
            continue
        if rows or not line:
            flush()
        if not line:
            continue
        heading = re.match(r"^(#{1,4})\s+(.*)$", line)
        if heading:
            flush()
            level = min(len(heading.group(1)) + 1, 5)       # the page owns <h1>
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
        elif line.startswith("- ") or re.match(r"^\d+\.\s", line):
            this = "ul" if line.startswith("- ") else "ol"
            if para or (bullets and kind[0] != this):
                flush()
            kind[0] = this
            bullets.append(line[2:] if this == "ul" else re.sub(r"^\d+\.\s+", "", line))
        else:
            if bullets:
                flush()
            para.append(line)
    flush()
    return "\n".join(out)


# ------------------------------------------------------------------ helpers
def _ok(**data: Any) -> str:
    return json.dumps({"ok": True, **data})


def _fail(message: str) -> str:
    return json.dumps({"ok": False, "error": message})


def _run_payload(run: service.DemoRun, zip_name: str) -> str:
    return _ok(
        summary_html=md_to_html(service.summary_markdown(run)),
        report_html=run.report_html,
        zip_b64=base64.b64encode(run.bundle.read_bytes()).decode("ascii"),
        zip_name=zip_name,
        wrong_links=run.score["wrong_links"] if run.score else None,
        reviews=run.result.summary["reviews"],
    )


def _tie_breaker(label: str, allowed: tuple[str, ...]) -> str:
    if label not in allowed:
        raise service.DemoError(f"Pick one of: {', '.join(allowed)}.")
    return service.TIE_BREAKERS[label]


# ------------------------------------------------------------------ entry points
def synthetic(difficulty: str, seed: Any, tie_breaker: str) -> str:
    try:
        key = _tie_breaker(tie_breaker, BROWSER_TIE_BREAKERS)
        run = service.run_synthetic(difficulty, seed, key)
        return _run_payload(run, f"reconloop-{difficulty}-{int(seed)}-{key}.zip")
    except service.DemoError as exc:
        return _fail(str(exc))
    except Exception as exc:  # noqa: BLE001 - the page shows the message instead of a stack trace
        return _fail(f"That run failed: {type(exc).__name__}: {exc}")


def uploaded(paths_json: str, tie_breaker: str) -> str:
    """`paths_json` maps the three expected file names to paths the page wrote into
    Pyodide's in-memory filesystem. Nothing leaves the browser."""
    try:
        key = _tie_breaker(tie_breaker, BROWSER_TIE_BREAKERS[:2])
        run = service.run_uploaded(json.loads(paths_json), key)
        return _run_payload(run, "reconloop-your-batch.zip")
    except service.DemoError as exc:
        return _fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        return _fail(f"Those files could not be reconciled: {type(exc).__name__}: {exc}")


def benchmark(seed_spec: str, difficulties_json: str, tie_breakers_json: str) -> str:
    try:
        try:
            parse_seeds(seed_spec, limit=BROWSER_MAX_SEEDS)
        except ValueError as exc:
            raise service.DemoError(str(exc).replace("seeds at a time", "seeds at a time in the browser")) from None
        labels = json.loads(tie_breakers_json)
        keys = [_tie_breaker(t, BROWSER_TIE_BREAKERS) for t in labels]
        md, bundle = service.quick_bench(seed_spec, json.loads(difficulties_json), keys)
        return _ok(table_html=md_to_html(md),
                   zip_b64=base64.b64encode(bundle.read_bytes()).decode("ascii"),
                   zip_name="reconloop-benchmark.zip")
    except service.DemoError as exc:
        return _fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        return _fail(f"The benchmark failed: {type(exc).__name__}: {exc}")


def sample_zip() -> str:
    return _ok(zip_b64=base64.b64encode(service.sample_bundle().read_bytes()).decode("ascii"),
               zip_name="reconloop-sample-batch.zip")
