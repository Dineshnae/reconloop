"""Run the agent over many generated batches and aggregate the scores."""

from __future__ import annotations

import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from . import io
from .agent import ReconAgent
from .config import DEFAULT, Config
from .generate import generate
from .resolver import ClaudeResolver, GreedyResolver, OfflineResolver, OracleResolver, Resolver
from .score import SCENARIO_LABELS, score

# Seeds looked at while building (development, then the first held-out run).
SEEDS_SEEN_WHILE_BUILDING = {7, 8, 101, 102, *range(201, 211)}

RESOLVER_NOTES = {
    "offline": "rules only; unclear cases go to a person",
    "greedy": "baseline that always takes the top rule candidate (no abstaining)",
    "oracle": "reads the answer key; the ceiling a perfect tie-breaker could reach",
    "claude": "Claude picks among candidates; every answer re-verified by the rules",
}


def make_resolver(name: str, truth: dict[str, Any] | None = None, model: str | None = None) -> Resolver:
    if name == "offline":
        return OfflineResolver()
    if name == "greedy":
        return GreedyResolver()
    if name == "oracle":
        if truth is None:
            raise ValueError("the oracle tie-breaker needs ground_truth.json in the batch folder")
        return OracleResolver(truth)
    if name == "claude":
        return ClaudeResolver(model=model)
    raise ValueError(f"unknown tie-breaker {name!r}")


def _seed(text: str) -> int:
    try:
        return int(text.strip())
    except ValueError:
        raise ValueError(f"{text.strip()!r} is not a seed; use a range like 401-410 or a list like 5,9,12") from None


def parse_seeds(spec: str, limit: int | None = None) -> list[int]:
    """'301-310' or '5,9,12' -> seeds. Raises ValueError on junk or more than `limit` seeds."""
    seeds: list[int] = []
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        if part.startswith("-"):
            raise ValueError("seeds must be zero or positive")
        if "-" in part:
            a, b = (_seed(x) for x in part.split("-", 1))
            if b < a:
                raise ValueError(f"range {part!r} runs backwards")
            if limit is not None and len(seeds) + (b - a + 1) > limit:
                raise ValueError(f"at most {limit} seeds at a time")
            seeds.extend(range(a, b + 1))
        else:
            seeds.append(_seed(part))
        if limit is not None and len(seeds) > limit:
            raise ValueError(f"at most {limit} seeds at a time")
    if any(x < 0 for x in seeds):
        raise ValueError("seeds must be zero or positive")
    return list(dict.fromkeys(seeds))


def _flat(seed: int, diff: str, rname: str, result, sc: dict[str, Any]) -> dict[str, Any]:
    L = sc["links"]
    tb = result.summary["tie_breaker"]
    tokens = tb.get("tokens") or {}
    return {
        "seed": seed, "difficulty": diff, "tie_breaker": rname,
        "records": result.summary["records"],
        "sb_precision": L["settlement_bank"]["precision"], "sb_recall": L["settlement_bank"]["recall"],
        "pi_precision": L["payment_invoice"]["precision"], "pi_recall": L["payment_invoice"]["recall"],
        "rc_precision": L["refund_credit_note"]["precision"], "rc_recall": L["refund_credit_note"]["recall"],
        "wrong_links": sc["wrong_links"],
        "exc_precision": sc["exceptions"]["precision"], "exc_recall": sc["exceptions"]["recall"],
        "reviews": sc["reviews"],
        "abstained_correctly": sc["abstained_correctly"],
        "undecidable_cases": sc["undecidable_cases"],
        "cost": sc["cost"]["total"], "cost_per_100": sc["cost"]["per_100_records"],
        "tie_breaker_cases": tb["cases"],
        "model_calls": tb.get("calls", 0),
        "input_tokens": tokens.get("input_tokens", 0), "output_tokens": tokens.get("output_tokens", 0),
        "runtime_s": result.summary["runtime_seconds"],
        "scenarios": sc["scenarios"],
    }


def aggregate(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in runs:
        groups[(r["difficulty"], r["tie_breaker"])].append(r)
    out = []
    for (diff, rname), rs in groups.items():
        row: dict[str, Any] = {"difficulty": diff, "tie_breaker": rname, "batches": len(rs)}
        for k in ("sb_precision", "sb_recall", "pi_precision", "pi_recall", "rc_precision", "rc_recall",
                  "exc_precision", "exc_recall", "reviews", "cost_per_100", "runtime_s", "records"):
            vals = [r[k] for r in rs]
            row[k] = round(statistics.mean(vals), 4)
            row[k + "_min"] = min(vals)
        row["wrong_links_total"] = sum(r["wrong_links"] for r in rs)
        row["wrong_links_worst_batch"] = max(r["wrong_links"] for r in rs)
        row["abstained_correctly"] = sum(r["abstained_correctly"] for r in rs)
        row["undecidable_cases"] = sum(r["undecidable_cases"] for r in rs)
        row["model_calls"] = sum(r["model_calls"] for r in rs)
        row["input_tokens"] = sum(r["input_tokens"] for r in rs)
        row["output_tokens"] = sum(r["output_tokens"] for r in rs)
        scen: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for r in rs:
            for name, counts in r["scenarios"].items():
                for k, v in counts.items():
                    scen[name][k] += v
        row["scenarios"] = {k: dict(v) for k, v in scen.items()}
        out.append(row)
    order = {"standard": 0, "hard": 1}
    tb_order = {"offline": 0, "greedy": 1, "claude": 2, "oracle": 3}
    return sorted(out, key=lambda r: (order.get(r["difficulty"], 9), tb_order.get(r["tie_breaker"], 9)))


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def _seed_note(seeds: list[int]) -> str:
    span = f"Seeds {seeds[0]}-{seeds[-1]}" if seeds == list(range(seeds[0], seeds[-1] + 1)) else \
        "Seeds " + ", ".join(str(x) for x in seeds)
    seen = sorted(set(seeds) & SEEDS_SEEN_WHILE_BUILDING)
    if seen:
        return (f"{span} ({len(seeds)} batches per difficulty). Includes seeds used while building "
                f"({', '.join(str(x) for x in seen)}), so treat these as development numbers.")
    return f"{span} ({len(seeds)} batches per difficulty), generated fresh and never inspected while building."


def to_markdown(agg: list[dict[str, Any]], seeds: list[int], command: str) -> str:
    lines = [
        "# Benchmark results",
        "",
        _seed_note(seeds),
        "",
        f"Command: `{command}`",
        "",
        "| Batch | Tie-breaker | Settlement to bank (P / R) | Payment to invoice (P / R) | Refund to credit note (P / R) | Wrong links (all batches) | Exceptions (P / R) | Reviews per batch | Error cost per 100 records |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in agg:
        lines.append(
            f"| {r['difficulty']} | {r['tie_breaker']} "
            f"| {_pct(r['sb_precision'])} / {_pct(r['sb_recall'])} "
            f"| {_pct(r['pi_precision'])} / {_pct(r['pi_recall'])} "
            f"| {_pct(r['rc_precision'])} / {_pct(r['rc_recall'])} "
            f"| {r['wrong_links_total']} "
            f"| {_pct(r['exc_precision'])} / {_pct(r['exc_recall'])} "
            f"| {r['reviews']:.1f} | {r['cost_per_100']:.2f} |"
        )
    lines += ["", "P = precision, R = recall, both averaged over batches. Error cost: 5 per wrong link, "
              "3 per missed exception, 1 per false exception, 1 per item sent to review.", ""]
    lines += ["Tie-breakers:", ""]
    for name in dict.fromkeys(r["tie_breaker"] for r in agg):
        lines.append(f"- **{name}**: {RESOLVER_NOTES.get(name, '')}")
    lines.append("")
    for r in agg:
        lines += [
            f"## Payment outcomes by scenario: {r['difficulty']}, {r['tie_breaker']}",
            "",
            "| Scenario | Payments | Correct | Sent to review | Wrong | Missed |",
            "|---|---|---|---|---|---|",
        ]
        for key, c in r["scenarios"].items():
            lines.append(f"| {SCENARIO_LABELS.get(key, key)} | {c.get('payments', 0)} | {c.get('correct', 0)} "
                         f"| {c.get('review', 0)} | {c.get('wrong', 0)} | {c.get('missed', 0)} |")
        extra = ""
        if r["undecidable_cases"]:
            extra = f" Undecidable cases correctly sent to a person: {r['abstained_correctly']} of {r['undecidable_cases']}."
        if r["model_calls"]:
            extra += f" Model calls: {r['model_calls']}, tokens in/out: {r['input_tokens']}/{r['output_tokens']}."
        lines += ["", f"Mean runtime per batch: {r['runtime_s']:.3f} s for about {r['records']:.0f} records.{extra}", ""]
    return "\n".join(lines) + "\n"


def run_bench(
    seeds: list[int], difficulties: list[str], tie_breakers: list[str], out: Path,
    cfg: Config = DEFAULT, model: str | None = None, command: str = "",
) -> dict[str, Any]:
    runs = []
    for diff in difficulties:
        for seed in seeds:
            data = out / "data" / f"{diff}-{seed}"
            generate(data, seed=seed, difficulty=diff, cfg=cfg)
            batch = io.load_batch(data)
            truth = io.load_truth(data)
            for name in tie_breakers:
                resolver = make_resolver(name, truth, model)
                result = ReconAgent(cfg, resolver).run(batch)
                runs.append(_flat(seed, diff, name, result, score(truth, result, cfg)))
    agg = aggregate(runs)
    io.write_json(out / "results.json", {"seeds": seeds, "runs": runs, "aggregate": agg})
    (out / "results.md").write_text(to_markdown(agg, seeds, command), encoding="utf-8")
    return {"runs": runs, "aggregate": agg}
