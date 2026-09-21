"""Pull Razorpay's settlement recon report into razorpay_recon.csv.

Endpoint: GET https://api.razorpay.com/v1/settlements/recon/combined
Auth: HTTP basic with key_id:key_secret. Query: year, month, optional day,
plus count/skip paging.

Status: written against the documented report shape and covered by a unit
test with a fake HTTP opener. It has NOT been run against a live account.
The recon report does not carry payer email or phone; join those from the
Payments API if you want contact matching.
"""

from __future__ import annotations

import base64
import json
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .. import io

API = "https://api.razorpay.com/v1/settlements/recon/combined"


def fetch_recon(
    key_id: str, key_secret: str, year: int, month: int, day: int | None = None,
    page_size: int = 100, opener: Callable[..., Any] = urllib.request.urlopen, max_pages: int = 500,
) -> list[dict[str, Any]]:
    token = base64.b64encode(f"{key_id}:{key_secret}".encode()).decode()
    items: list[dict[str, Any]] = []
    for page in range(max_pages):
        query = {"year": year, "month": month, "count": page_size, "skip": page * page_size}
        if day:
            query["day"] = day
        req = urllib.request.Request(f"{API}?{urllib.parse.urlencode(query)}",
                                     headers={"Authorization": f"Basic {token}"})
        with opener(req, timeout=30) as resp:
            batch = json.load(resp).get("items", [])
        items.extend(batch)
        if len(batch) < page_size:
            break
    return items


def write_recon_csv(items: list[dict[str, Any]], path: Path) -> None:
    rows = []
    for it in items:
        row = {c: it.get(c) for c in io.RZP_COLUMNS}
        notes = it.get("notes")
        row["notes"] = json.dumps(notes, sort_keys=True) if isinstance(notes, dict) and notes else ""
        for flag in ("on_hold", "settled"):
            row[flag] = "true" if it.get(flag) else "false"
        rows.append({k: ("" if v is None else v) for k, v in row.items()})
    io.write_csv(Path(path), rows, io.RZP_COLUMNS)
