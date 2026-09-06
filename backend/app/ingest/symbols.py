"""Which ticker is this instrument, and who decides. M2 spec section 6.

Deliberately the same shape as `ingest/corporate_actions.py`: candidates are
detected, the ones that can be proved are accepted, the rest go to a review table
that is a PROJECTION rather than a queue, and the answers live in a gitignored,
hand-edited YAML file beside the ledger. One pattern for "the machine is unsure,
a human decides", not two.

What differs is the burden of proof. A corporate action is quarantined because
the export is ambiguous; a symbol is quarantined because a confident, correct-
looking answer can still be the wrong instrument. M2 spec section 3.2: roughly
one ISIN in nine resolved to a leveraged or inverse ETF on the same underlying,
and every one of those passed the checks an implementation would naturally apply.
So nothing here accepts a candidate on the strength of the lookup. The ledger
decides, in `domain/symbols.py`, and this module only carries rows to it.

An answer file entry wins outright and is never probed. A human has decided, and
spending a provider call to second-guess them would also quarantine their answer
on any day the network disagreed.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol
from uuid import uuid4

import yaml
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.domain.orders import LedgerRow
from app.domain.splits import Split, derive_splits
from app.domain.symbols import (
    CandidateSeries,
    TradeObservation,
    Verdict,
    accepted_symbol,
    judge,
)
from app.models.market import SymbolReview
from app.providers.base import PriceProvider, SymbolCandidate, SymbolResolver

#: What an operator writes to route an instrument to `manual_prices.csv`.
MANUAL_ANSWER = "manual"

_ANSWER_FIELDS = frozenset({"isin", "symbol", "note"})


class MalformedSymbolAnswers(ValueError):
    """The answers file exists but cannot be trusted. Names the file and entry."""


class PricedRow(LedgerRow, Protocol):
    """`LedgerRow` plus the two fields symbol resolution reads.

    `currency_local` because a provider quotes a series in its own currency and
    the comparison has to happen there. `product_name` because
    `models.ledger.Instrument` is declared but nothing populates it, so the
    ledger row is where an instrument's name actually lives.
    """

    currency_local: str | None
    product_name: str | None


@dataclass(frozen=True, slots=True)
class SymbolAnswer:
    """One human answer. `symbol is None` means "route this to the price file"."""

    isin: str
    symbol: str | None
    note: str = ""


@dataclass(frozen=True, slots=True)
class PendingSymbol:
    """One instrument still waiting on a human, with the evidence attached."""

    isin: str
    product_name: str
    trade_currency: str
    verdicts: tuple[Verdict, ...]


@dataclass(frozen=True, slots=True)
class ResolutionReport:
    #: ISIN -> symbol, or None for "answered `manual`".
    resolved: dict[str, str | None]
    pending: tuple[PendingSymbol, ...]

    @property
    def ok(self) -> bool:
        return not self.pending


def load_symbol_answers(path: Path) -> dict[str, SymbolAnswer]:
    """Read the operator's answers. A missing file answers nothing.

    Strict about shape for the reason `load_resolutions` is, and then some. A
    corporate-action typo produces a suppressed trade, which the reconciliation
    invariants catch. A symbol typo produces a plausible price series for the
    wrong instrument, and nothing downstream can tell.
    """
    if not path.exists():
        return {}

    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as broken:
        raise MalformedSymbolAnswers(f"{path}: not valid YAML -- {broken}") from broken
    if not isinstance(document, dict):
        raise MalformedSymbolAnswers(f"{path}: expected a mapping at the top level")

    answers: dict[str, SymbolAnswer] = {}
    for position, entry in enumerate(document.get("symbols") or [], start=1):
        where = f"{path}: entry {position}"
        if not isinstance(entry, dict):
            raise MalformedSymbolAnswers(f"{where} is not a mapping")

        unknown = sorted(set(entry) - _ANSWER_FIELDS)
        if unknown:
            raise MalformedSymbolAnswers(
                f"{where} has unknown field(s) {unknown}; expected {sorted(_ANSWER_FIELDS)}"
            )

        isin = entry.get("isin")
        if not isinstance(isin, str) or not isin.strip():
            raise MalformedSymbolAnswers(f"{where} needs a non-empty string 'isin'")
        if isin in answers:
            raise MalformedSymbolAnswers(f"{path}: {isin!r} is answered twice")

        symbol = entry.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            raise MalformedSymbolAnswers(
                f"{where} ({isin}) needs a non-empty 'symbol', or {MANUAL_ANSWER!r}"
            )
        cleaned = symbol.strip()
        answers[isin] = SymbolAnswer(
            isin=isin,
            symbol=None if cleaned == MANUAL_ANSWER else cleaned,
            note=str(entry.get("note", "")),
        )
    return answers


def _is_priced_trade(row: PricedRow) -> bool:
    return (
        row.is_economic
        and row.isin is not None
        and row.quantity is not None
        and row.quantity != 0
        and row.price_local is not None
        and row.currency_local is not None
    )


def observations(rows: Sequence[PricedRow]) -> dict[str, list[TradeObservation]]:
    """Every executed price the ledger holds, per instrument, oldest first.

    Sells as well as buys: a sale is an executed price too, and an instrument
    whose buys predate the export window has nothing else to check against.
    """
    found: dict[str, list[TradeObservation]] = defaultdict(list)
    for row in rows:
        if not _is_priced_trade(row):
            continue
        found[str(row.isin)].append(
            TradeObservation(
                trade_date=row.trade_date,
                price_local=abs(row.price_local or Decimal("0")),
                currency=str(row.currency_local),
            )
        )
    return {
        isin: sorted(seen, key=lambda o: o.trade_date) for isin, seen in found.items()
    }


def instrument_names(rows: Sequence[PricedRow]) -> dict[str, str]:
    """ISIN -> the name the broker printed beside it."""
    names: dict[str, str] = {}
    for row in rows:
        if row.isin and row.product_name:
            names.setdefault(row.isin, row.product_name)
    return names


def _candidate_series(
    candidates: Sequence[SymbolCandidate], prices: PriceProvider
) -> list[CandidateSeries]:
    """Fetch a series for each candidate ticker, so the ledger can judge it.

    A candidate with no series is dropped rather than judged: the discriminator
    would report NO_CLOSE_NEAR_TRADE, which reads as "the wrong instrument" when
    the truth is "nothing came back at all".
    """
    found: list[CandidateSeries] = []
    for candidate in candidates:
        series = prices.full_series(candidate.symbol)
        if series is None or not series.points:
            continue
        found.append(
            CandidateSeries(
                symbol=candidate.symbol,
                currency=series.currency,
                closes={point.on: point.close_unadjusted for point in series.points},
            )
        )
    return found


def resolve_symbols(
    rows: Sequence[PricedRow],
    *,
    answers: Mapping[str, SymbolAnswer],
    resolver: SymbolResolver,
    prices: PriceProvider,
) -> ResolutionReport:
    """Decide a symbol for every instrument the ledger ever held.

    An answered instrument is taken as answered. Everything else is probed, and
    accepted only if exactly one candidate clears all three conditions of M2 spec
    section 6.2 against the ledger's own executed prices.
    """
    by_isin = observations(rows)
    names = instrument_names(rows)
    splits = derive_splits(rows)

    resolved: dict[str, str | None] = {}
    pending: list[PendingSymbol] = []

    # One batch call for everything an answer file entry did not already
    # settle -- a deterministic, sorted list, never a set, so the resolver's
    # positional match has something stable to match against. An instrument
    # already answered is never in this list and so is never probed.
    to_probe = [isin for isin in sorted(by_isin) if isin not in answers]
    candidates_by_isin = resolver.candidates_for(to_probe)

    for isin in sorted(by_isin):
        if isin in answers:
            resolved[isin] = answers[isin].symbol
            continue

        trades = by_isin[isin]
        for_this: Sequence[Split] = [s for s in splits if s.isin == isin]
        verdicts = judge(
            _candidate_series(candidates_by_isin.get(isin, ()), prices), trades, for_this
        )

        chosen = accepted_symbol(verdicts)
        if chosen is not None:
            resolved[isin] = chosen
            continue

        pending.append(
            PendingSymbol(
                isin=isin,
                product_name=names.get(isin, ""),
                trade_currency=trades[0].currency if trades else "",
                verdicts=verdicts,
            )
        )

    return ResolutionReport(resolved=resolved, pending=tuple(pending))


def _as_json(verdicts: Sequence[Verdict]) -> str:
    return json.dumps(
        [
            {
                "symbol": verdict.symbol,
                "accepted": verdict.accepted,
                "reason": verdict.reason,
                # Strings, not floats: a ratio is money-derived and the operator
                # is reading it to decide, so it must be the number measured.
                "ratios": [str(ratio) for ratio in verdict.ratios],
            }
            for verdict in verdicts
        ]
    )


def write_symbol_review(
    engine: Engine, report: ResolutionReport, *, detected_at: datetime
) -> int:
    """Rewrite the review table to describe this run. Returns rows written.

    A replacement, not an append: the queue holds no state of its own, the
    answers do. A queue that accumulated would keep asking questions already
    answered, which is how a quarantine trains people to ignore it.
    """
    with Session(engine) as session:
        for stale in session.exec(select(SymbolReview)).all():
            session.delete(stale)
        session.flush()
        for item in report.pending:
            session.add(
                SymbolReview(
                    id=uuid4(),
                    isin=item.isin,
                    product_name=item.product_name,
                    trade_currency=item.trade_currency,
                    candidates=_as_json(item.verdicts),
                    detected_at=detected_at,
                )
            )
        session.commit()
    return len(report.pending)
