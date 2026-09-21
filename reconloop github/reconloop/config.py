"""Tunable knobs for the reconciliation agent.

Everything that decides whether money gets linked lives here, so a reviewer can
see the whole risk posture in one file. Rules and thresholds were developed
against seeds 7, 8, 101 and 102. Reported benchmark numbers come from seeds
301-310. Seeds 201-210 were a first held-out run that exposed two generator
bugs (see docs/BUILD_LOG.md); no matching rule changed after that run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping


def _frozen(d: dict) -> Mapping:
    return MappingProxyType(dict(d))


@dataclass(frozen=True)
class RateCard:
    """Merchant's contracted fee rates per payment method.

    SYNTHETIC. These are not Razorpay's published prices; swap in the
    merchant's real contract. Fee base = amount x rate, GST = 18% of base,
    and Razorpay's `fee` column carries base + GST with `tax` = GST.
    """

    rates: Mapping[str, Decimal] = field(
        default_factory=lambda: _frozen(
            {
                "card": Decimal("0.0195"),
                "netbanking": Decimal("0.0180"),
                "wallet": Decimal("0.0195"),
                "upi": Decimal("0.0000"),
            }
        )
    )
    gst_rate: Decimal = Decimal("0.18")


@dataclass(frozen=True)
class Tolerances:
    fee_paise: int = 1                     # rounding slack on fee base
    tax_paise: int = 1                     # rounding slack on GST
    amount_mismatch_ratio: Decimal = Decimal("0.10")  # link + flag inside this band
    bank_lag_days: int = 3                 # settlement can land in bank up to T+3
    book_window_days: int = 2              # payment vs invoice date window
    duplicate_window_minutes: int = 30


@dataclass(frozen=True)
class Weights:
    """Evidence weights for payment -> invoice scoring (sum of parts, capped at 1)."""

    reference: float = 0.60
    contact: float = 0.50
    amount_exact: float = 0.30
    amount_near: float = 0.10
    same_day: float = 0.10
    next_day: float = 0.05
    name: float = 0.50                     # multiplied by name similarity (0-1)


@dataclass(frozen=True)
class Thresholds:
    auto_link: float = 0.80                # minimum evidence score to link without review
    margin: float = 0.15                   # best must beat runner-up by this much
    name_evidence: float = 0.60            # name similarity that counts as identity evidence
    resolver_confidence: float = 0.75      # model decisions below this go to a human
    subset_max_size: int = 3


@dataclass(frozen=True)
class CostModel:
    """How the benchmark prices mistakes. A wrong link is the expensive one:
    money gets marked reconciled when it is not."""

    wrong_link: int = 5
    missed_exception: int = 3
    false_exception: int = 1
    review_item: int = 1


@dataclass(frozen=True)
class Config:
    rate_card: RateCard = field(default_factory=RateCard)
    tol: Tolerances = field(default_factory=Tolerances)
    weights: Weights = field(default_factory=Weights)
    thresholds: Thresholds = field(default_factory=Thresholds)
    cost: CostModel = field(default_factory=CostModel)


DEFAULT = Config()
