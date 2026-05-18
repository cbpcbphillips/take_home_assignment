"""StormlandHoldings — worked ontology example."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Optional
from uuid import UUID

import us
from moneyed import USD, Money as _Money
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer
from stdnum.us import ein as _ein


# ---- Markers + validators ---------------------------------------------------
# Pattern: each field type is `Annotated[base_type, BeforeValidator, Hint]`.
#   * BeforeValidator normalizes whatever the LLM produces into a canonical form.
#   * Hint describes that canonical form so prompt-rendering can show it to
#     the LLM up front — fewer round trips through ValidationError.

class Hint:
    """Free-text description for LLM agents, carried as Annotated metadata."""
    __slots__ = ("text",)

    def __init__(self, text: str):
        self.text = text


def _to_state(v: str) -> str:
    state = us.states.lookup(v)
    if state is None:
        raise ValueError(f"unknown US state: {v!r}")
    return state.abbr


def _to_ein(v: str) -> str:
    return _ein.format(_ein.validate(v))


def _to_money(v) -> _Money:
    if isinstance(v, _Money):
        return v
    if isinstance(v, dict):
        return _Money(v["amount"], v.get("currency", "USD"))
    return _Money(str(v), USD)


USState = Annotated[
    str,
    BeforeValidator(_to_state),
    Hint("USPS 2-letter US state code, e.g. 'CA'. Full names like 'California' are accepted and normalized."),
]
EIN = Annotated[
    str,
    BeforeValidator(_to_ein),
    Hint("US Employer Identification Number formatted 'NN-NNNNNNN'. Bare 9-digit strings are accepted and dashed."),
]
Money = Annotated[
    _Money,
    BeforeValidator(_to_money),
    PlainSerializer(lambda m: {"amount": str(m.amount), "currency": str(m.currency)}),
    Hint("currency-aware money amount; bare numbers are normalized as USD."),
]


# ---- Entities ---------------------------------------------------------------

class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)


class Property(_Base):
    address: str               = Field(min_length=1)
    state:   Optional[USState] = None


class Tenant(_Base):
    name: str           = Field(min_length=1)
    ein:  Optional[EIN] = None


class Landlord(_Base):
    name: str           = Field(min_length=1)
    ein:  Optional[EIN] = None


class Lease(_Base):
    tenant_id:         Optional[UUID] = None
    landlord_id:       Optional[UUID] = None
    premises_id:       Optional[UUID] = None
    start_date:        date
    base_rent_monthly: Money
    end_date:          Optional[date] = None


REGISTRY: dict[str, type[BaseModel]] = {
    "Property": Property,
    "Tenant":   Tenant,
    "Landlord": Landlord,
    "Lease":    Lease,
}
