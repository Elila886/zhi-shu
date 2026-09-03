from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from uuid import uuid4

from app.leave.schemas import DecisionInput, LeaveBalanceInput
from app.leave.service import calculate_duration


def test_half_day_and_weekend_exclusion_are_calculated_server_side():
    assert calculate_duration(date(2026, 9, 4), date(2026, 9, 4), "pm", "pm", True) == Decimal("0.5")
    # Friday afternoon through Monday morning excludes the weekend.
    assert calculate_duration(date(2026, 9, 4), date(2026, 9, 7), "pm", "am", True) == Decimal("1.0")


@pytest.mark.parametrize("start,end,start_period,end_period", [
    (date(2026, 9, 7), date(2026, 9, 4), "am", "pm"),
    (date(2026, 9, 5), date(2026, 9, 7), "am", "pm"),
    (date(2026, 9, 7), date(2027, 1, 4), "am", "pm"),
    (date(2026, 9, 7), date(2026, 9, 7), "pm", "am"),
])
def test_invalid_leave_ranges_are_rejected(start, end, start_period, end_period):
    with pytest.raises(HTTPException):
        calculate_duration(start, end, start_period, end_period, True)


def test_non_half_day_leave_type_rejects_half_day_input():
    with pytest.raises(HTTPException):
        calculate_duration(date(2026, 9, 7), date(2026, 9, 7), "am", "am", False)


def test_rejection_requires_a_comment():
    with pytest.raises(ValidationError):
        DecisionInput(decision="rejected", version=1, idempotency_key="12345678")
    assert DecisionInput(decision="approved", version=1, idempotency_key="12345678").decision == "approved"


def test_balance_entitlement_requires_half_day_precision():
    with pytest.raises(ValidationError):
        LeaveBalanceInput(leave_type_id=uuid4(), year=2026, entitled_days=Decimal("1.1"))
    assert LeaveBalanceInput(leave_type_id=uuid4(), year=2026, entitled_days=Decimal("1.5")).entitled_days == Decimal("1.5")
