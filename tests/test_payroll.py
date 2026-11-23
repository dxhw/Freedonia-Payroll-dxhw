import copy
import json

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.law_repository import LawRepository
from app.main import app

client = TestClient(app)

### Constants
EMPLOYEE = "emp-001"
EXEMPT_DATE_OF_BIRTH = "1900-02-12"
NON_EXEMPT_DATE_OF_BIRTH = "1996-02-12"
FIRST_OF_MONTH_DATE_OF_BIRTH = "1966-03-01"
OLD_LAW_VERSION = "2026.1.1"
NEW_LAW_VERSION = "2026.7.1"

DEFAULT_REQUEST = {
        "employee": {
            "id": EMPLOYEE,
            "resident": True,
            "date_of_birth": NON_EXEMPT_DATE_OF_BIRTH
        },
        "gross_pay": "10000.00",
        "pay_period": "2026-03",
        "pay_date": "2026-03-31"
    }

### Helpers
def line_assertion(body, ee_contribution, tax, er_contribution, bases):
    pension_ee = body["lines"][0]
    income_tax = body["lines"][1]
    pension_er = body["lines"][2]
    assert pension_ee["code"] == "PENSION_EE"
    assert pension_ee["party"] == "employee"
    assert pension_ee["amount"] == ee_contribution
    assert pension_ee["basis"] == bases[0]

    assert income_tax["code"] == "INCOME_TAX"
    assert income_tax["party"] == "employee"
    assert income_tax["amount"] == tax
    assert income_tax["basis"] == bases[1]

    assert pension_er["code"] == "PENSION_ER"
    assert pension_er["party"] == "employer"
    assert pension_er["amount"] == er_contribution
    assert pension_er["basis"] == bases[2]

### Standard Behavior Tests
# exempt vs non-exempt
# resident vs non-resident
# includes vs excludes amendment
# rounding: rounds down

def test_non_exempt_resident_unamended():
    payload = DEFAULT_REQUEST
    response = client.post("/payslips/calculate", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["employee_id"] == EMPLOYEE
    assert body["gross_pay"] == "10000.00"

    line_bases = [
        "§5(1): employee contribution of 6% of gross pay 10000.00.",
        (
            "§2, §3, §4(a), §6: taxable income 9400.00 is gross pay less the employee "
            "pension contribution; tax of 940.00 on 9400.00 at 10%; less personal relief "
            "of FRD 2400.00, but not below zero; the employer withholds 0.00."
        ),
        "§5(1): employer contribution of 6% of gross pay 10000.00.",
    ]
    line_assertion(body, "600.00", "0.00", "600.00", line_bases)

    assert body["net_pay"] == "9400.00"
    assert body["law_version"] == OLD_LAW_VERSION

def test_exempt_resident_amended():
    payload = copy.deepcopy(DEFAULT_REQUEST)
    payload["gross_pay"] = "30000.00"
    payload["pay_period"] = "2026-06"
    payload["pay_date"] = "2026-07-01"
    payload["employee"]["date_of_birth"] = EXEMPT_DATE_OF_BIRTH
    response = client.post("/payslips/calculate", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["employee_id"] == EMPLOYEE
    assert body["gross_pay"] == "30000.00"

    line_bases = [
        (
            "§5(3): employee attained age 60 before 2026-07-01, the first day of the "
            "payment month, so no employee contribution is required."
        ),
        (
            "§2, §3, §4(a), §4(b) as amended by Finance (Amendment) Act 2026 s.2, "
            "§6 as amended by Finance (Amendment) Act 2026 s.3: taxable income 30000.00 "
            "is gross pay less the employee pension contribution; tax of 4050.00 on "
            "24000.00 at 10%, 6000.00 at 27.5%; less personal relief of FRD 2500.00; "
            "the employer withholds 1550.00."
        ),
        (
            "§5(1), §5(2): employer contribution of 6% of gross pay 30000.00, capped at FRD 1080.00 per payment; "
            "still payable although the employee is exempt."
        ),
    ]
    line_assertion(body, "0.00", "1550.00", "1080.00", line_bases)

    assert body["net_pay"] == "28450.00"
    assert body["law_version"] == NEW_LAW_VERSION

# 20% of 10000.03 is 2000.006: 2000.00 rounded down, 2000.01 rounded half-up
def test_non_resident():
    payload = copy.deepcopy(DEFAULT_REQUEST)
    payload["employee"]["resident"] = False
    payload["gross_pay"] = "10000.03"
    response = client.post("/payslips/calculate", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["employee_id"] == EMPLOYEE
    assert body["gross_pay"] == "10000.03"

    line_bases = [
        "§7(a): non-resident, so no pension contribution is payable.",
        (
            "§2, §7(a), §7(b): non-resident, so the resident rules do not apply; "
            "flat tax of 20% of gross pay 10000.03; the employer withholds 2000.00."
        ),
        "§7(a): non-resident, so no pension contribution is payable.",
    ]
    line_assertion(body, "0.00", "2000.00", "0.00", line_bases)

    assert body["net_pay"] == "8000.03"
    assert body["law_version"] == OLD_LAW_VERSION

### Edge cases
# turning 60 on the first of the payment month is not "before" it
def test_resident_turning_60_on_first_of_month_is_not_exempt():
    payload = copy.deepcopy(DEFAULT_REQUEST)
    payload["employee"]["date_of_birth"] = FIRST_OF_MONTH_DATE_OF_BIRTH
    response = client.post("/payslips/calculate", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["employee_id"] == EMPLOYEE
    assert body["gross_pay"] == "10000.00"

    line_bases = [
        "§5(1): employee contribution of 6% of gross pay 10000.00.",
        (
            "§2, §3, §4(a), §6: taxable income 9400.00 is gross pay less the employee "
            "pension contribution; tax of 940.00 on 9400.00 at 10%; less personal relief "
            "of FRD 2400.00, but not below zero; the employer withholds 0.00."
        ),
        "§5(1): employer contribution of 6% of gross pay 10000.00.",
    ]
    line_assertion(body, "600.00", "0.00", "600.00", line_bases)

    assert body["net_pay"] == "9400.00"
    assert body["law_version"] == OLD_LAW_VERSION

# per-band rounding would give tax of 2191.59
def test_band_tax_is_rounded_once_in_total():
    payload = copy.deepcopy(DEFAULT_REQUEST)
    payload["gross_pay"] = "33413.09"
    payload["pay_period"] = "2026-07"
    payload["pay_date"] = "2026-07-31"
    response = client.post("/payslips/calculate", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["employee_id"] == EMPLOYEE
    assert body["gross_pay"] == "33413.09"

    line_bases = [
        (
            "§5(1), §5(2): employee contribution of 6% of gross pay 33413.09, "
            "capped at FRD 1080.00 per payment."
        ),
        (
            "§2, §3, §4(a), §4(b) as amended by Finance (Amendment) Act 2026 s.2, "
            "§4(c), §6 as amended by Finance (Amendment) Act 2026 s.3: taxable income "
            "32333.09 is gross pay less the employee pension contribution; tax of "
            "4691.60 on 24000.00 at 10%, 8333.00 at 27.5%, 0.09 at 30%; less personal "
            "relief of FRD 2500.00; the employer withholds 2191.60."
        ),
        (
            "§5(1), §5(2): employer contribution of 6% of gross pay 33413.09, "
            "capped at FRD 1080.00 per payment."
        ),
    ]
    line_assertion(body, "1080.00", "2191.60", "1080.00", line_bases)

    assert body["net_pay"] == "30141.49"
    assert body["law_version"] == NEW_LAW_VERSION

# This case does not show up with only the single amendment in the problem statement, 
# but is an important behavior for the system broadly for correct law-choosing
def test_amendment_stacks_on_earlier_amendment(tmp_path, laws_dir, monkeypatch):
    for name in ("freedonia-2026.json", "freedonia-2026-amended.json"):
        (tmp_path / name).write_text(
            (laws_dir / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    (tmp_path / "freedonia-2027.json").write_text(
        json.dumps({
            "effective_from": "2027-01-01",
            "changes": {
                # override the 2026 amendment's personal relief provision
                "resident.tax.personal_relief": "2000",
                "resident.tax.relief_provision": "§6 as amended by Finance Act 2027 s.1",
                "resident.pension.cap": "1200",
                "resident.pension.cap_provision": "§5(2) as amended by Finance Act 2027 s.2",
            },
        }),
        encoding="utf-8",
    )
    repository = LawRepository(tmp_path)
    repository.load_all()
    monkeypatch.setattr(main_module, "law_repository", repository)

    payload = copy.deepcopy(DEFAULT_REQUEST)
    payload["gross_pay"] = "30000.00"
    payload["pay_period"] = "2027-01"
    payload["pay_date"] = "2027-01-01"
    response = client.post("/payslips/calculate", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["employee_id"] == EMPLOYEE
    assert body["gross_pay"] == "30000.00"

    # the 27.5% band still applies from the earlier amendment
    # the 2500 FRD relief has been amended
    line_bases = [
        (
            "§5(1), §5(2) as amended by Finance Act 2027 s.2: employee contribution "
            "of 6% of gross pay 30000.00, capped at FRD 1200.00 per payment."
        ),
        (
            "§2, §3, §4(a), §4(b) as amended by Finance (Amendment) Act 2026 s.2, "
            "§6 as amended by Finance Act 2027 s.1: taxable income 28800.00 "
            "is gross pay less the employee pension contribution; tax of 3720.00 on "
            "24000.00 at 10%, 4800.00 at 27.5%; less personal relief of FRD 2000.00; "
            "the employer withholds 1720.00."
        ),
        (
            "§5(1), §5(2) as amended by Finance Act 2027 s.2: employer contribution "
            "of 6% of gross pay 30000.00, capped at FRD 1200.00 per payment."
        ),
    ]
    line_assertion(body, "1200.00", "1720.00", "1200.00", line_bases)

    assert body["net_pay"] == "27080.00"
    assert body["law_version"] == "2027.1.1"


### Errors

@pytest.mark.parametrize("gross_pay", [
    12345678901234567.89,
    30000,
    "100.009",
    "1E+4",
    "1e30",
    " 100.00",
    "100.00\n",
    "NaN",
    "",
    "1000000000000.00",
    None,
])
def test_malformed_gross_pay_returns_400(gross_pay):
    payload = copy.deepcopy(DEFAULT_REQUEST)
    payload["gross_pay"] = gross_pay
    response = client.post("/payslips/calculate", json=payload)
    assert response.status_code == 400
    assert "gross_pay" in response.json()["error"]

# the day before the original Act takes effect
def test_no_effective_law_returns_400():
    payload = copy.deepcopy(DEFAULT_REQUEST)
    payload["pay_period"] = "2025-12"
    payload["pay_date"] = "2025-12-31"
    response = client.post("/payslips/calculate", json=payload)
    assert response.status_code == 400
    assert response.json()["error"] == (
        "pay_date: No applicable Freedonia law found for 2025-12-31"
    )
