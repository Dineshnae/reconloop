import zipfile
from types import SimpleNamespace

import pytest

from reconloop import io, service
from reconloop.agent import ReconAgent
from reconloop.resolver import TOOL_NAME, ClaudeResolver


def test_synthetic_run_is_scored_and_bundled():
    run = service.run_synthetic("hard", 301, "offline")
    assert run.score is not None and run.score["wrong_links"] == 0
    names = zipfile.ZipFile(run.bundle).namelist()
    assert "input/razorpay_recon.csv" in names and "output/report.html" in names
    md = service.summary_markdown(run)
    assert "Settlements found in the bank" in md and "Wrong links" in md
    assert "Scored against the answer key" in md


def test_uploaded_batch_runs_without_an_answer_key():
    files = {n: str(service.SAMPLE_DIR / n) for n in (io.RZP_FILE, io.BANK_FILE, io.BOOKS_FILE)}
    run = service.run_uploaded(files, "offline")
    assert run.score is None
    assert "not scored" in service.summary_markdown(run)
    assert run.result.links


@pytest.mark.parametrize("bad,message", [
    ({}, "Add razorpay_recon.csv"),
    ({"books": "wrong header"}, "missing column"),
    ({"books": b"\xff\xfe\x00bad"}, "not UTF-8"),
])
def test_upload_problems_get_a_plain_message(tmp_path, bad, message):
    files = {n: str(service.SAMPLE_DIR / n) for n in (io.RZP_FILE, io.BANK_FILE, io.BOOKS_FILE)}
    if not bad:
        files.pop(io.RZP_FILE)
    else:
        broken = tmp_path / io.BOOKS_FILE
        payload = bad["books"]
        broken.write_bytes(payload if isinstance(payload, bytes) else payload.encode())
        files[io.BOOKS_FILE] = str(broken)
    with pytest.raises(service.DemoError) as exc:
        service.run_uploaded(files, "offline")
    assert message in str(exc.value)


def test_oracle_needs_an_answer_key_and_claude_needs_a_key():
    with pytest.raises(service.DemoError):
        service.build_resolver("oracle", None)
    if not service.claude_available():
        with pytest.raises(service.DemoError):
            service.build_resolver("claude", None)


def test_bad_seeds_and_oversized_benchmarks_are_refused():
    for args in [("hard", "abc", "offline"), ("hard", -1, "offline"), ("nonsense", 1, "offline")]:
        with pytest.raises(service.DemoError):
            service.run_synthetic(*args)
    for spec in ("1-50", "abc", ""):
        with pytest.raises(service.DemoError):
            service.quick_bench(spec, ["standard"], ["offline"])


def test_quick_bench_returns_a_table_and_a_zip():
    md, bundle = service.quick_bench("401,402", ["standard"], ["offline", "greedy"])
    assert "Wrong links" in md and "| standard | offline |" in md
    assert sorted(zipfile.ZipFile(bundle).namelist()) == ["results.json", "results.md"]


def _fake_client():
    block = SimpleNamespace(type="tool_use", name=TOOL_NAME,
                            input={"choice": "NONE", "confidence": 0.9, "reason": "nothing fits"})
    resp = SimpleNamespace(content=[block], usage=SimpleNamespace(input_tokens=10, output_tokens=5))
    return SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: resp))


def test_model_budget_stops_calls_and_falls_back_to_a_person():
    inner = ClaudeResolver(client=_fake_client(), cache_dir=None, model="test-model")
    budget = service.ModelBudget(1)
    resolver = service.BudgetedClaude(inner, per_run=5, budget=budget)
    case = {"payment": {"id": "pay_1"}, "candidates": [{"id": "INV/2026-27/00101"}]}
    first, second = resolver.decide(case), resolver.decide(case)
    assert first.choice == "NONE" and first.error is None
    assert second.choice == "UNSURE" and second.error == "budget_exhausted"
    assert budget.left == 0 and resolver.calls == 1 and resolver.model == "test-model"


def test_budget_exhaustion_sends_cases_to_review_not_to_a_guess():
    batch = io.load_batch(service.SAMPLE_DIR)
    inner = ClaudeResolver(client=_fake_client(), cache_dir=None, model="test-model")
    resolver = service.BudgetedClaude(inner, per_run=0, budget=service.ModelBudget(0))
    result = ReconAgent(resolver=resolver).run(batch)
    assert not [l for l in result.links if l.stage.startswith("tie_breaker")]
    assert result.reviews and resolver.refused == len(result.resolver_log)
