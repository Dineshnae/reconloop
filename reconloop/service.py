"""Plumbing behind the web demo. No web framework imports, so it is tested directly.

Every run gets its own temporary folder (input/, output/, a zip of both).
Folders older than an hour are swept on the next run.
"""

from __future__ import annotations

import csv
import os
import shutil
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import io
from .agent import INFO_CODES, ReconAgent, RunResult, write_outputs
from .bench import make_resolver, parse_seeds, run_bench
from .config import DEFAULT
from .generate import generate
from .money import inr
from .report import headline, render_report
from .resolver import UNSURE, ClaudeResolver, Decision
from .score import score

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "data" / "sample"
PUBLISHED_RESULTS = ROOT / "bench" / "results.md"
TMP_PREFIX = "reconloop-"
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_BENCH_SEEDS = 20
MAX_SEED = 10**9

TIE_BREAKERS = {
    "Rules only": "offline",
    "Greedy baseline": "greedy",
    "Oracle ceiling": "oracle",
    "Claude": "claude",
}
LABELS = {v: k for k, v in TIE_BREAKERS.items()}

REQUIRED_COLUMNS = {
    io.RZP_FILE: ["entity_id", "type", "debit", "credit", "amount", "fee", "tax", "created_at"],
    io.BANK_FILE: ["line_id", "txn_date", "narration", "debit", "credit"],
    io.BOOKS_FILE: ["doc_no", "doc_type", "doc_date", "customer_name", "amount"],
}


HOW_MD = """### The loop

1. **Ingest.** Rows that cannot be read are quarantined and reported, never silently dropped.
2. **Control checks.** Fee against the contracted rate card, GST on the fee, double captures on one order, chargeback debits.
3. **Settlement to bank.** Full UTR, then a UTR fragment unique to one settlement, then exact amount inside the T+0 to T+3 window, with ties broken by the bank lag learned from the confident matches. Still tied means a person looks at it.
4. **Payment to invoice.** Receipt, then a cleaned-up reference, then identity evidence (payer contact, names in the note) scored against amount and date, then subset-sum for split and combined payments.
5. **Tie-breaker.** Only what is left: about 3 to 5% of payments.
6. **Refund to credit note**, then open invoices, then proposed actions.

### Where a model is used, and where it is not

Settlements, fees, GST, duplicates and subset-sum are arithmetic, so they stay deterministic. The model
only reads payer notes such as "Kiran from Brightpath, office order" when two invoices fit equally well.

It sees amounts, dates, customer names, the note, and the evidence the rules computed. It never sees
email addresses or phone numbers: contact matching happens locally. It answers with one of the invoice
ids it was shown, NONE, or UNSURE, and the rules reject anything else.

### Guardrails

- A payment is never linked on amount and date alone.
- The model's pick is overruled if it names an id it was not offered, if confidence is under 0.75, if it
  says no match while contact or reference evidence points at a candidate, or if the call fails.
- Proposed actions are drafts. Anything that moves money or posts to the ledger needs approval, carries an
  amount ceiling and has a stable idempotency key. Nothing executes.
"""


class DemoError(ValueError):
    """A problem the person using the demo can fix. The message is shown as-is."""


# ------------------------------------------------------------------ Claude budget
def claude_available() -> bool:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


class ModelBudget:
    """Process-wide cap on live model calls, so a public demo cannot run up a bill."""

    def __init__(self, total: int):
        self.total = total
        self.used = 0
        self._lock = threading.Lock()

    def take(self) -> bool:
        with self._lock:
            if self.used >= self.total:
                return False
            self.used += 1
            return True

    @property
    def left(self) -> int:
        return max(0, self.total - self.used)


BUDGET = ModelBudget(int(os.environ.get("RECONLOOP_DEMO_CALL_BUDGET", "300")))
CALLS_PER_RUN = int(os.environ.get("RECONLOOP_DEMO_CALLS_PER_RUN", "25"))


class BudgetedClaude:
    """Claude tie-breaker with a per-run and a per-process cap. Cached answers are free.
    Past the cap, cases go to a person, which is the same path as any model failure."""

    name = "claude"

    def __init__(self, inner: ClaudeResolver, per_run: int, budget: ModelBudget):
        self.inner = inner
        self.per_run = per_run
        self.budget = budget
        self.spent = 0
        self.refused = 0

    model = property(lambda self: self.inner.model)
    calls = property(lambda self: self.inner.calls)
    cache_hits = property(lambda self: self.inner.cache_hits)
    tokens = property(lambda self: self.inner.tokens)

    def decide(self, case: dict[str, Any]) -> Decision:
        if not self.inner.is_cached(case):
            if self.spent >= self.per_run or not self.budget.take():
                self.refused += 1
                return Decision(UNSURE, 0.0, "The demo's model budget is used up; routed to a person.",
                                self.name, self.inner.model, error="budget_exhausted")
            self.spent += 1
        return self.inner.decide(case)


def build_resolver(key: str, truth: dict[str, Any] | None):
    if key == "claude":
        if not claude_available():
            raise DemoError("Claude is switched off in this deployment. Pick another tie-breaker.")
        inner = ClaudeResolver(cache_dir=os.environ.get("RECONLOOP_CACHE_DIR", str(ROOT / ".cache" / "resolver")))
        return BudgetedClaude(inner, CALLS_PER_RUN, BUDGET)
    if key == "oracle" and truth is None:
        raise DemoError("The oracle reads the answer key, and uploaded files do not have one. Pick another tie-breaker.")
    if key not in LABELS:
        raise DemoError(f"Unknown tie-breaker {key!r}.")
    return make_resolver(key, truth)


# ------------------------------------------------------------------ runs
@dataclass
class DemoRun:
    result: RunResult
    score: dict[str, Any] | None
    report_html: str
    bundle: Path
    tie_breaker: str


def sweep(max_age_seconds: int = 3600) -> None:
    now = time.time()
    for d in Path(tempfile.gettempdir()).glob(TMP_PREFIX + "*"):
        try:
            if d.is_dir() and now - d.stat().st_mtime > max_age_seconds:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            continue


def _workdir() -> Path:
    sweep()
    return Path(tempfile.mkdtemp(prefix=TMP_PREFIX))


def _zip(folder: Path, target: Path, parts: tuple[str, ...]) -> Path:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for part in parts:
            base = folder / part
            if not base.exists():
                continue
            for f in sorted(base.rglob("*")):
                if f.is_file():
                    zf.write(f, f"{part}/{f.relative_to(base)}")
    return target


def run_folder(data: Path, key: str) -> DemoRun:
    batch = io.load_batch(data)
    truth = io.load_truth(data)
    resolver = build_resolver(key, truth)
    result = ReconAgent(DEFAULT, resolver).run(batch)
    sc = score(truth, result) if truth else None
    work = data.parent
    out = work / "output"
    write_outputs(result, out)
    if sc:
        io.write_json(out / "score.json", sc)
    report = render_report(result, sc)
    (out / "report.html").write_text(report, encoding="utf-8")
    bundle = _zip(work, work / "reconloop-run.zip", ("input", "output"))
    return DemoRun(result, sc, report, bundle, key)


def run_synthetic(difficulty: str, seed: Any, key: str) -> DemoRun:
    if difficulty not in ("standard", "hard"):
        raise DemoError("Difficulty must be standard or hard.")
    try:
        seed_i = int(seed)
    except (TypeError, ValueError):
        raise DemoError("The seed must be a whole number.") from None
    if not 0 <= seed_i <= MAX_SEED:
        raise DemoError(f"Pick a seed between 0 and {MAX_SEED:,}.")
    work = _workdir()
    try:
        generate(work / "input", seed=seed_i, difficulty=difficulty)
    except RuntimeError as exc:
        raise DemoError(f"Seed {seed_i} could not be generated ({exc}). Try another seed.") from None
    return run_folder(work / "input", key)


def _check_headers(path: Path, name: str) -> None:
    with path.open(newline="", encoding="utf-8") as fh:
        header = [h.strip() for h in next(csv.reader(fh), [])]
    missing = [c for c in REQUIRED_COLUMNS[name] if c not in header]
    if missing:
        raise DemoError(f"{name} is missing column(s): {', '.join(missing)}. "
                        "Download the sample batch to see the expected layout.")


def run_uploaded(files: dict[str, str | None], key: str) -> DemoRun:
    work = _workdir()
    data = work / "input"
    data.mkdir(parents=True)
    for name in (io.RZP_FILE, io.BANK_FILE, io.BOOKS_FILE):
        src = files.get(name)
        if not src:
            raise DemoError(f"Add {name} to run your own batch.")
        src_path = Path(src)
        if src_path.stat().st_size > MAX_UPLOAD_BYTES:
            raise DemoError(f"{name} is over 5 MB; this demo keeps uploads small.")
        try:
            text = src_path.read_bytes().decode("utf-8-sig")
        except UnicodeDecodeError:
            raise DemoError(f"{name} is not UTF-8 text. Re-save it as CSV UTF-8.") from None
        (data / name).write_text(text, encoding="utf-8")
        _check_headers(data / name, name)
    return run_folder(data, key)


def sample_bundle() -> Path:
    """The demo batch as a zip, so people can see the expected CSV layout."""
    target = Path(tempfile.gettempdir()) / "reconloop-sample-batch.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in (io.RZP_FILE, io.BANK_FILE, io.BOOKS_FILE, io.META_FILE, io.TRUTH_FILE):
            if (SAMPLE_DIR / name).exists():
                zf.write(SAMPLE_DIR / name, name)
    return target


# ------------------------------------------------------------------ summaries
def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def summary_markdown(run: DemoRun) -> str:
    s = run.result.summary
    st, pay, tb = s["settlements"], s["payments"], s["tie_breaker"]
    attention = sum(1 for e in run.result.exceptions if e.code not in INFO_CODES)
    considered = pay["count"] - pay["duplicates_set_aside"]
    lines = [
        f"### {headline(run.result)}",
        "",
        "| | |",
        "|---|---|",
        f"| Settlements found in the bank | {st['in_bank']} of {st['count']} ({inr(st['in_bank_paise'])} of {inr(st['net_paise'])}) |",
        f"| Payments tied to invoices | {pay['linked']} of {considered} ({pay['duplicates_set_aside']} double charges set aside) |",
        f"| Refunds with a credit note | {s['refunds']['linked']} of {s['refunds']['count']} |",
        f"| Exceptions raised | {attention} ({inr(s['exceptions_paise'])} at stake) |",
        f"| Sent to a person | {s['reviews']} |",
        f"| Tie-breaker | {LABELS.get(run.tie_breaker, run.tie_breaker)}, asked about {tb['cases']} unclear payments |",
        f"| Time | {s['runtime_seconds']:.3f} s for {s['records']} records |",
    ]
    if s["inputs"]["quarantined"]:
        lines.append(f"| Unreadable rows set aside | {s['inputs']['quarantined']} |")
    if "calls" in tb:
        tok = tb.get("tokens") or {}
        lines.append(f"| Model usage | {tb['calls']} calls, {tb.get('cache_hits', 0)} cached, "
                     f"{tok.get('input_tokens', 0):,} tokens in / {tok.get('output_tokens', 0):,} out; "
                     f"{BUDGET.left} calls left in this demo |")
    if run.score:
        sc = run.score
        pi, ex = sc["links"]["payment_invoice"], sc["exceptions"]
        wrong = sc["wrong_links"]
        lines += [
            "",
            "#### Scored against the answer key",
            "",
            "| | |",
            "|---|---|",
            f"| Wrong links | **{wrong}**{' (money marked reconciled that is not)' if wrong else ''} |",
            f"| Payment to invoice | precision {_pct(pi['precision'])}, recall {_pct(pi['recall'])} |",
            f"| Settlement to bank | precision {_pct(sc['links']['settlement_bank']['precision'])}, "
            f"recall {_pct(sc['links']['settlement_bank']['recall'])} |",
            f"| Exceptions | precision {_pct(ex['precision'])}, recall {_pct(ex['recall'])} |",
            f"| Undecidable cases sent to a person | {sc['abstained_correctly']} of {sc['undecidable_cases']} |",
            f"| Error cost | {sc['cost']['total']} units, {sc['cost']['per_100_records']} per 100 records |",
        ]
    else:
        lines += ["", "No answer key came with these files, so accuracy is not scored."]
    return "\n".join(lines)


def published_results() -> str:
    if not PUBLISHED_RESULTS.exists():
        return "No published benchmark in this deployment."
    return PUBLISHED_RESULTS.read_text(encoding="utf-8").split("\n## ")[0].replace("# Benchmark results", "").strip()


def quick_bench(seed_spec: str, difficulties: list[str], keys: list[str]) -> tuple[str, Path]:
    try:
        seeds = parse_seeds(seed_spec, limit=MAX_BENCH_SEEDS)
    except ValueError as exc:
        raise DemoError(str(exc)) from None
    if not seeds:
        raise DemoError("Give at least one seed.")
    if any(x > MAX_SEED for x in seeds):
        raise DemoError(f"Seeds go up to {MAX_SEED:,}.")
    diffs = [d for d in difficulties if d in ("standard", "hard")]
    if not diffs:
        raise DemoError("Pick at least one difficulty.")
    keys = [k for k in keys if k in ("offline", "greedy", "oracle")]
    if not keys:
        raise DemoError("Pick at least one tie-breaker.")
    work = _workdir()
    try:
        run_bench(seeds, diffs, keys, work, DEFAULT, command=f"web demo: seeds {seed_spec}")
    except RuntimeError as exc:
        raise DemoError(f"A batch could not be generated ({exc}). Try other seeds.") from None
    md = (work / "results.md").read_text(encoding="utf-8")
    bundle = work / "reconloop-benchmark.zip"
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(work / "results.md", "results.md")
        zf.write(work / "results.json", "results.json")
    shutil.rmtree(work / "data", ignore_errors=True)
    return md.split("\n## ")[0].replace("# Benchmark results", "").strip(), bundle
