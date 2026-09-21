"""reconloop generate | run | bench | demo | fetch-razorpay"""

from __future__ import annotations

import argparse
import os
import shlex
import sys
from pathlib import Path

from . import io
from .agent import INFO_CODES, ReconAgent, RunResult, write_outputs
from .bench import make_resolver, parse_seeds, run_bench
from .config import DEFAULT
from .generate import generate
from .money import inr
from .report import render_report
from .score import score


def _print_summary(result: RunResult, sc: dict | None, report: Path) -> None:
    s = result.summary
    st, pay = s["settlements"], s["payments"]
    attention = sum(1 for e in result.exceptions if e.code not in INFO_CODES)
    head = s.get("merchant") or "Batch"
    if s.get("period"):
        head += f", {s['period'][0]} to {s['period'][1]}"
    if s.get("seed") is not None:
        head += f" (seed {s['seed']}, {s['difficulty']})"
    print(head)
    print(f"  Settlements in bank     {st['in_bank']} of {st['count']}   {inr(st['in_bank_paise'])} of {inr(st['net_paise'])}")
    print(f"  Payments to invoices    {pay['linked']} of {pay['count'] - pay['duplicates_set_aside']}"
          f"   ({pay['duplicates_set_aside']} double charges set aside)")
    print(f"  Refunds to credit notes {s['refunds']['linked']} of {s['refunds']['count']}")
    print(f"  Exceptions              {attention}   ({inr(s['exceptions_paise'])} at stake)")
    print(f"  Sent to review          {s['reviews']}")
    tb = s["tie_breaker"]
    extra = f", {tb.get('calls', 0)} model calls, {tb.get('cache_hits', 0)} cached" if "calls" in tb else ""
    print(f"  Tie-breaker             {tb['name']} ({tb['cases']} cases{extra})")
    if sc:
        pi, ex = sc["links"]["payment_invoice"], sc["exceptions"]
        print("Scored against the answer key")
        print(f"  Wrong links             {sc['wrong_links']}")
        print(f"  Payment to invoice      precision {100 * pi['precision']:.1f}%  recall {100 * pi['recall']:.1f}%")
        print(f"  Exceptions              precision {100 * ex['precision']:.1f}%  recall {100 * ex['recall']:.1f}%")
        print(f"  Error cost              {sc['cost']['total']} units ({sc['cost']['per_100_records']} per 100 records)")
    print(f"Report: {report}")


def _run(data: Path, out: Path, tie_breaker: str, model: str | None) -> int:
    batch = io.load_batch(data)
    truth = io.load_truth(data)
    if tie_breaker == "oracle":
        print("Note: the oracle reads the answer key. Use it only as a ceiling.", file=sys.stderr)
    try:
        resolver = make_resolver(tie_breaker, truth, model)
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    result = ReconAgent(DEFAULT, resolver).run(batch)
    write_outputs(result, out)
    sc = score(truth, result) if truth else None
    if sc:
        io.write_json(out / "score.json", sc)
    report = out / "report.html"
    report.write_text(render_report(result, sc), encoding="utf-8")
    _print_summary(result, sc, report)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="reconloop",
        description="Three-way settlement reconciliation for Razorpay merchants, graded against an answer key.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    tb_choices = ["offline", "greedy", "oracle", "claude"]

    g = sub.add_parser("generate", help="write a synthetic batch and its answer key")
    g.add_argument("--out", required=True, type=Path)
    g.add_argument("--seed", type=int, default=7)
    g.add_argument("--difficulty", choices=["standard", "hard"], default="standard")

    r = sub.add_parser("run", help="reconcile a batch folder and write the report")
    r.add_argument("--data", required=True, type=Path)
    r.add_argument("--out", required=True, type=Path)
    r.add_argument("--tie-breaker", choices=tb_choices, default="offline")
    r.add_argument("--model", help="Claude model id (default: $RECONLOOP_MODEL or claude-sonnet-5)")

    b = sub.add_parser("bench", help="score the agent on fresh held-out batches")
    b.add_argument("--seeds", default="301-310")
    b.add_argument("--difficulty", default="standard,hard")
    b.add_argument("--tie-breaker", default="offline,greedy,oracle")
    b.add_argument("--out", type=Path, default=Path("bench"))
    b.add_argument("--model")
    b.add_argument("--max-wrong-links", type=int, default=None,
                   help="exit 1 if any batch has more wrong links than this (CI gate)")
    b.add_argument("--gate", default="offline", help="tie-breaker the gate applies to")

    d = sub.add_parser("demo", help="generate the sample batch, reconcile it, write the report")
    d.add_argument("--data", type=Path, default=Path("data/sample"))
    d.add_argument("--out", type=Path, default=Path("out/demo"))
    d.add_argument("--tie-breaker", choices=tb_choices, default="offline")
    d.add_argument("--model")

    f = sub.add_parser("fetch-razorpay", help="pull a real settlement recon report (untested against a live account)")
    f.add_argument("--year", type=int, required=True)
    f.add_argument("--month", type=int, required=True)
    f.add_argument("--day", type=int)
    f.add_argument("--out", type=Path, required=True)

    args = ap.parse_args(argv)

    if args.cmd == "generate":
        meta = generate(args.out, seed=args.seed, difficulty=args.difficulty)
        print(f"Wrote {args.out}: {meta['counts']}")
        return 0

    if args.cmd == "run":
        return _run(args.data, args.out, args.tie_breaker, args.model)

    if args.cmd == "demo":
        if not (args.data / io.RZP_FILE).exists():
            generate(args.data, seed=7, difficulty="standard")
        return _run(args.data, args.out, args.tie_breaker, args.model)

    if args.cmd == "bench":
        seeds = parse_seeds(args.seeds)
        diffs = [x.strip() for x in args.difficulty.split(",") if x.strip()]
        tbs = [x.strip() for x in args.tie_breaker.split(",") if x.strip()]
        command = "python -m reconloop " + " ".join(shlex.quote(a) for a in (argv if argv is not None else sys.argv[1:]))
        try:
            res = run_bench(seeds, diffs, tbs, args.out, DEFAULT, args.model, command)
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print((args.out / "results.md").read_text().split("\n## ")[0])
        if args.max_wrong_links is not None:
            worst = max((r["wrong_links"] for r in res["runs"] if r["tie_breaker"] == args.gate), default=0)
            if worst > args.max_wrong_links:
                print(f"GATE FAILED: {args.gate} made {worst} wrong links in one batch "
                      f"(allowed {args.max_wrong_links}).", file=sys.stderr)
                return 1
            print(f"Gate passed: {args.gate} stayed at or under {args.max_wrong_links} wrong links per batch.")
        return 0

    if args.cmd == "fetch-razorpay":
        from .connectors.razorpay_api import fetch_recon, write_recon_csv

        key_id, secret = os.environ.get("RAZORPAY_KEY_ID"), os.environ.get("RAZORPAY_KEY_SECRET")
        if not key_id or not secret:
            print("error: set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET", file=sys.stderr)
            return 2
        items = fetch_recon(key_id, secret, args.year, args.month, args.day)
        write_recon_csv(items, args.out / io.RZP_FILE)
        print(f"Wrote {len(items)} rows to {args.out / io.RZP_FILE}. Add bank_statement.csv and books.csv, then run.")
        return 0
    return 2
