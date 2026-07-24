"""tests/test_models.py"""

from datetime import date

from custom_components.aquawatch.models import (
    ConsumptionBatch,
    ConsumptionRecord,
    ContractInfo,
)


def test_consumption_record_fields() -> None:
    record = ConsumptionRecord(
        record_date=date(2024, 3, 15),
        liters=150.0,
        cumulative_index_m3=100.0,
        is_estimated=False,
    )
    assert record.liters == 150.0
    assert record.is_estimated is False


def test_consumption_batch_holds_records_and_price() -> None:
    record = ConsumptionRecord(
        record_date=date(2024, 3, 15),
        liters=150.0,
        cumulative_index_m3=100.0,
        is_estimated=False,
    )
    batch = ConsumptionBatch(records=[record], price_per_m3=4.2)
    assert batch.records == [record]
    assert batch.price_per_m3 == 4.2


def test_contract_info_fields() -> None:
    contract = ContractInfo(contract_id="CTR-1", label="Contrat CTR-1")
    assert contract.contract_id == "CTR-1"
