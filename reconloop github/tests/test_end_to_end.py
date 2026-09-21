import json

from reconloop import io
from reconloop.agent import ReconAgent
from reconloop.bench import run_bench
from reconloop.cli import main
from reconloop.config import DEFAULT
from reconloop.resolver import OracleResolver
from reconloop.score import score


def test_rules_alone_make_no_wrong_links(loaded):
    batch, truth = loaded
    sc = score(truth, ReconAgent(DEFAULT).run(batch))
    assert sc["wrong_links"] == 0
    assert sc["links"]["settlement_bank"]["recall"] == 1.0
    assert sc["links"]["payment_invoice"]["recall"] > 0.95
    assert sc["exceptions"]["precision"] == 1.0
    assert sc["abstained_correctly"] == sc["undecidable_cases"]


def test_oracle_ceiling_is_perfect_on_the_dev_seed(loaded):
    batch, truth = loaded
    sc = score(truth, ReconAgent(DEFAULT, OracleResolver(truth)).run(batch))
    assert sc["wrong_links"] == 0
    assert sc["links"]["payment_invoice"]["recall"] == 1.0
    assert sc["exceptions"]["recall"] == 1.0


def test_money_moving_actions_are_gated_and_bounded(loaded):
    batch, _ = loaded
    result = ReconAgent(DEFAULT).run(batch)
    risky = [a for a in result.actions if a["moves_money"] or a["posts_to_ledger"]]
    assert risky
    for a in risky:
        assert a["requires_approval"] and a["status"] == "proposed"
        assert isinstance(a["max_amount_paise"], int) and a["max_amount_paise"] > 0
    keys = [a["idempotency_key"] for a in result.actions]
    assert len(keys) == len(set(keys))
    again = ReconAgent(DEFAULT).run(batch)
    assert keys == [a["idempotency_key"] for a in again.actions]


def test_demo_command_writes_every_output(tmp_path, capsys):
    data, out = tmp_path / "data", tmp_path / "out"
    assert main(["demo", "--data", str(data), "--out", str(out)]) == 0
    for name in ("links.csv", "exceptions.csv", "review_queue.csv", "actions.json", "audit.jsonl",
                 "summary.json", "score.json", "report.html"):
        assert (out / name).exists(), name
    html = (out / "report.html").read_text()
    assert "Needs a decision" in html and "Waiting for your review" in html and "<script" not in html
    printed = capsys.readouterr().out
    assert "Wrong links             0" in printed
    events = [json.loads(line) for line in (out / "audit.jsonl").read_text().splitlines()]
    assert events and all("decision" in e for e in events)


def test_bench_gate(tmp_path):
    res = run_bench([7], ["standard"], ["offline", "oracle"], tmp_path)
    assert (tmp_path / "results.md").exists()
    assert {r["tie_breaker"] for r in res["aggregate"]} == {"offline", "oracle"}
    assert max(r["wrong_links"] for r in res["runs"]) == 0
    assert main(["bench", "--seeds", "7", "--difficulty", "standard", "--tie-breaker", "offline",
                 "--out", str(tmp_path / "gate"), "--max-wrong-links", "0"]) == 0


def test_unreadable_rows_are_reported_not_dropped(hard_batch):
    batch = io.load_batch(hard_batch)
    result = ReconAgent(DEFAULT).run(batch)
    assert [e.entity_id for e in result.exceptions if e.code == "MALFORMED_ROW"] == [q["entity_id"] for q in batch.quarantined]
