import json

import pytest

from reconloop.resolver import TOOL_NAME, ClaudeResolver

anthropic = pytest.importorskip("anthropic")
import anthropic._base_client as _bc  # noqa: E402

hx = getattr(_bc, "httpx2", None) or getattr(_bc, "httpx")

CASE = {
    "payment": {"id": "pay_1", "amount": "₹1,499.00", "date": "2026-08-05", "payer_note": "Venky - curtains"},
    "candidates": [{"id": "INV/2026-27/00123", "customer": "Venkatesh Iyer"},
                   {"id": "INV/2026-27/00124", "customer": "Harish Kumar"}],
}


def client(handler):
    return anthropic.Anthropic(api_key="test", max_retries=0, http_client=hx.Client(transport=hx.MockTransport(handler)))


def test_request_shape_parse_and_cache(tmp_path):
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        return hx.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": body["model"],
            "content": [{"type": "tool_use", "id": "t1", "name": TOOL_NAME,
                         "input": {"choice": "INV/2026-27/00123", "confidence": 0.86, "reason": "Venky is short for Venkatesh."}}],
            "stop_reason": "tool_use", "stop_sequence": None,
            "usage": {"input_tokens": 600, "output_tokens": 40},
        })

    r = ClaudeResolver(client=client(handler), cache_dir=tmp_path, model="claude-sonnet-5")
    first, second = r.decide(CASE), r.decide(CASE)
    assert (first.choice, first.confidence) == ("INV/2026-27/00123", 0.86)
    assert second.cached and r.calls == 1 and r.tokens == {"input_tokens": 600, "output_tokens": 40}
    body = seen[0]
    assert body["tool_choice"] == {"type": "tool", "name": TOOL_NAME, "disable_parallel_tool_use": True}
    assert body["tools"][0]["input_schema"]["properties"]["choice"]["enum"] == [
        "INV/2026-27/00123", "INV/2026-27/00124", "NONE", "UNSURE"]
    assert "@" not in json.dumps(body)


def test_overloaded_api_becomes_a_review(tmp_path):
    def handler(request):
        return hx.Response(529, json={"type": "error", "error": {"type": "overloaded_error", "message": "busy"}})

    d = ClaudeResolver(client=client(handler), cache_dir=None).decide(CASE)
    assert d.choice == "UNSURE" and d.error


def test_reply_without_tool_call_becomes_a_review():
    def handler(request):
        body = json.loads(request.content)
        return hx.Response(200, json={
            "id": "m", "type": "message", "role": "assistant", "model": body["model"],
            "content": [{"type": "text", "text": "Probably the first one."}],
            "stop_reason": "end_turn", "stop_sequence": None, "usage": {"input_tokens": 5, "output_tokens": 5},
        })

    d = ClaudeResolver(client=client(handler), cache_dir=None).decide(CASE)
    assert d.choice == "UNSURE" and d.error == "no_tool_use"
