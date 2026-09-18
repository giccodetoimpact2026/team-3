"""Typed outcomes for exchange writes.

A write never raises. The caller must be able to tell "the exchange refused
this" apart from "we don't know whether it landed", because only the second
case requires reconciliation against the idempotency key — and only the second
case makes a blind resubmission a duplicate order.

Error codes come from the error envelope in exchange-api-reference.md; the
`code` field is carried through verbatim so callers can branch on it
(PRICE_OUT_OF_BAND wants a reprice, INSUFFICIENT_FUNDS wants a smaller size).
"""
from dataclasses import dataclass
from enum import Enum


class Outcome(Enum):
    ACKNOWLEDGED = "acknowledged"   # 2xx and envelope status == "success"
    REJECTED = "rejected"           # 4xx / 503 with a code — do not retry as-is
    RATE_LIMITED = "rate_limited"   # 429 — definitively refused; back off
    RETRYABLE = "retryable"         # 500 — nothing landed; safe to retry
    UNKNOWN = "unknown"             # 504 / timeout — RECONCILE, never resubmit


class CancelOutcome(Enum):
    CANCELLED = "cancelled"               # 200
    NOT_CANCELLABLE = "not_cancellable"   # already filled or cancelled
    NOT_FOUND = "not_found"               # unknown id, or not ours
    RATE_LIMITED = "rate_limited"
    RETRYABLE = "retryable"
    UNKNOWN = "unknown"                   # timeout — the order may still be live


@dataclass(frozen=True)
class SubmitResult:
    outcome: Outcome
    idempotency_key: str
    order_id: str | None = None
    status: str | None = None          # OPEN | PARTIALLY_FILLED | FILLED
    remaining_qty: int = 0
    filled_qty: int = 0
    code: str | None = None
    retry_after: float | None = None
    rate_remaining: int | None = None  # X-RateLimit-Remaining for the orders tier

    @property
    def acknowledged(self) -> bool:
        return self.outcome is Outcome.ACKNOWLEDGED

    @property
    def landed_nothing(self) -> bool:
        """True when we know for certain no order was created."""
        return self.outcome in (Outcome.REJECTED, Outcome.RATE_LIMITED, Outcome.RETRYABLE)


@dataclass(frozen=True)
class CancelResult:
    outcome: CancelOutcome
    order_id: str
    code: str | None = None
    retry_after: float | None = None
    rate_remaining: int | None = None  # X-RateLimit-Remaining for the shared tier

    @property
    def settled(self) -> bool:
        """True when the order is known not to be live any more.

        NOT_CANCELLABLE and NOT_FOUND both mean "not resting": the order filled
        or was already cancelled. A shutdown treats those as done rather than
        as failures, which is how the already-filled race stays benign.
        """
        return self.outcome in (
            CancelOutcome.CANCELLED,
            CancelOutcome.NOT_CANCELLABLE,
            CancelOutcome.NOT_FOUND,
        )
