"""Tie-breakers for payment -> invoice cases the rules could not settle.

What the model sees: amounts, dates, customer names, the payer's note, and
evidence the rules already computed (contact match yes/no, name similarity,
days apart). It never sees emails or phone numbers.

What the model can say: one of the candidate ids it was shown, NONE (nothing
here is this payment's invoice) or UNSURE (ask a human). The agent re-checks
every answer before anything is linked; see BookMatcher._verify.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

NONE = "NONE"
UNSURE = "UNSURE"
TOOL_NAME = "submit_match_decision"
PROMPT_VERSION = "2026-09-v1"
DEFAULT_MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = """You are the tie-breaker in a payment reconciliation pipeline for an Indian D2C merchant.
Deterministic rules have already run. You only see payments they could not match with confidence.

Decide which candidate invoice this Razorpay payment pays for.

Rules:
- Answer with a candidate id from the list, or NONE, or UNSURE. Never invent an id.
- Read the payer note. Payments are often made by someone other than the invoiced customer
  (a family member, an employee paying for a business). Names show up as nicknames, initials,
  honorifics, abbreviations and transliterations.
- A similar spelling can be a different person. Do not treat near-miss names as the same name
  unless the note gives you a reason to.
- Equal amount and date alone never identify a customer. If nothing in the note, reference or
  contact evidence connects the payment to any candidate, answer NONE.
- If more than one candidate fits the evidence equally well, answer UNSURE. A wrong match costs
  five times more than sending the case to a human.
- confidence is your probability that your answer is correct.
- reason is one or two sentences naming the evidence you relied on.

Always answer by calling the submit_match_decision tool."""


@dataclass
class Decision:
    choice: str
    confidence: float
    reason: str
    source: str
    model: str | None = None
    error: str | None = None
    usage: dict[str, int] | None = None
    cached: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class Resolver(Protocol):
    name: str

    def decide(self, case: dict[str, Any]) -> Decision: ...


class OfflineResolver:
    """No model: every ambiguous case goes to a person."""

    name = "offline"

    def decide(self, case: dict[str, Any]) -> Decision:
        return Decision(UNSURE, 0.0, "No model configured; routed to a human.", self.name)


class ScriptedResolver:
    """Test double. `fn(case) -> Decision`."""

    name = "scripted"

    def __init__(self, fn: Callable[[dict[str, Any]], Decision]):
        self.fn = fn

    def decide(self, case: dict[str, Any]) -> Decision:
        return self.fn(case)


class OracleResolver:
    """Benchmark ceiling ONLY. Reads the answer key, so it shows how much a perfect
    tie-breaker could add. Never use its numbers as model results."""

    name = "oracle"

    def __init__(self, truth: dict[str, Any]):
        self.links: dict[str, set[str]] = defaultdict(set)
        for pay, inv in truth["links"]["payment_invoice"]:
            self.links[pay].add(inv)
        self.undecidable = {e["subject"] for e in truth.get("expected_review", [])}
        self.missing = {e["entity_id"] for e in truth["exceptions"] if e["code"] == "MISSING_INVOICE"}

    def decide(self, case: dict[str, Any]) -> Decision:
        pay = case["payment"]["id"]
        if pay in self.undecidable:
            return Decision(UNSURE, 1.0, "oracle: undecidable by design", self.name)
        hits = [c["id"] for c in case["candidates"] if c["id"] in self.links[pay]]
        if len(hits) == 1:
            return Decision(hits[0], 1.0, "oracle", self.name)
        if pay in self.missing:
            return Decision(NONE, 1.0, "oracle: no invoice exists", self.name)
        return Decision(UNSURE, 1.0, "oracle: true invoice not among candidates", self.name)


class GreedyResolver:
    """Baseline, not a model: always takes the candidate with the most rule evidence
    (cases list candidates best-first) and never abstains. Shows what an
    over-eager tie-breaker costs."""

    name = "greedy"

    def decide(self, case: dict[str, Any]) -> Decision:
        return Decision(case["candidates"][0]["id"], 0.9, "greedy: highest rule evidence", self.name)


def tool_schema(candidate_ids: list[str]) -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "description": (
            "Record the reconciliation decision for this payment. Call exactly once. "
            "choice must be one of the listed candidate ids, NONE, or UNSURE."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "choice": {"type": "string", "enum": [*candidate_ids, NONE, UNSURE]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "reason": {"type": "string"},
            },
            "required": ["choice", "confidence", "reason"],
        },
    }


def render_case(case: dict[str, Any]) -> str:
    return (
        "Which invoice does this payment settle?\n\n"
        + json.dumps(case, indent=2, ensure_ascii=False, sort_keys=True)
    )


class ClaudeResolver:
    name = "claude"

    def __init__(
        self,
        model: str | None = None,
        client: Any = None,
        cache_dir: str | Path | None = ".cache/resolver",
        max_tokens: int = 400,
        temperature: float | None = None,
    ):
        self.model = model or os.environ.get("RECONLOOP_MODEL", DEFAULT_MODEL)
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if client is None:
            try:
                import anthropic  # noqa: WPS433
            except ImportError as exc:  # pragma: no cover - environment specific
                raise RuntimeError("Claude mode needs the SDK: pip install 'reconloop[claude]'") from exc
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise RuntimeError("Claude mode needs ANTHROPIC_API_KEY in the environment.")
            client = anthropic.Anthropic()
        self.client = client
        self.calls = 0
        self.cache_hits = 0
        self.tokens = {"input_tokens": 0, "output_tokens": 0}

    def _key(self, case: dict[str, Any]) -> str:
        payload = json.dumps(case, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(f"{PROMPT_VERSION}|{self.model}|{payload}".encode()).hexdigest()

    def is_cached(self, case: dict[str, Any]) -> bool:
        return bool(self.cache_dir and (self.cache_dir / f"{self._key(case)}.json").exists())

    def decide(self, case: dict[str, Any]) -> Decision:
        key = self._key(case)
        path = self.cache_dir / f"{key}.json" if self.cache_dir else None
        if path and path.exists():
            self.cache_hits += 1
            d = Decision(**json.loads(path.read_text()))
            d.cached = True
            return d

        ids = [c["id"] for c in case["candidates"]]
        kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            system=SYSTEM_PROMPT,
            tools=[tool_schema(ids)],
            tool_choice={"type": "tool", "name": TOOL_NAME, "disable_parallel_tool_use": True},
            messages=[{"role": "user", "content": render_case(case)}],
        )
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        self.calls += 1
        try:
            resp = self.client.messages.create(**kwargs)
        except Exception as exc:  # network, auth, rate limit: never fatal
            return Decision(UNSURE, 0.0, "Model call failed; routed to a human.", self.name,
                            self.model, error=f"{type(exc).__name__}: {exc}"[:300])

        usage = getattr(resp, "usage", None)
        used = {
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        }
        for k, v in used.items():
            self.tokens[k] += v

        block = next((b for b in getattr(resp, "content", []) if getattr(b, "type", None) == "tool_use"), None)
        if block is None or getattr(block, "name", None) != TOOL_NAME:
            return Decision(UNSURE, 0.0, "Model did not return a decision.", self.name, self.model,
                            error="no_tool_use", usage=used)
        data = block.input if isinstance(block.input, dict) else {}
        try:
            confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0
        decision = Decision(
            choice=str(data.get("choice", UNSURE)),
            confidence=confidence,
            reason=str(data.get("reason", ""))[:300],
            source=self.name,
            model=self.model,
            usage=used,
        )
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(decision.as_dict(), sort_keys=True))
        return decision
