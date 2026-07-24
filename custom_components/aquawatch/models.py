"""Shared data models for AquaWatch."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class ConsumptionRecord:
    """A single daily water consumption measurement."""

    record_date: date
    liters: float
    cumulative_index_m3: float
    is_estimated: bool


@dataclass(frozen=True)
class ContractInfo:
    """A water contract/meter available on a provider account."""

    contract_id: str
    label: str


@dataclass(frozen=True)
class ConsumptionBatch:
    """A batch of consumption records plus the price used to cost them."""

    records: list[ConsumptionRecord]
    price_per_m3: float
