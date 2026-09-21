"""Append-only decision log. Every link, exception and model call lands here."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class AuditLog:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def record(self, stage: str, subject: str, decision: str, **detail: Any) -> None:
        self.events.append(
            {"seq": len(self.events) + 1, "stage": stage, "subject": subject, "decision": decision, **detail}
        )

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for e in self.events:
                fh.write(json.dumps(e, sort_keys=True, default=str) + "\n")

    def for_subject(self, subject: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["subject"] == subject]
