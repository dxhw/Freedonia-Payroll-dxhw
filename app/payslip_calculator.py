
"""Freedonia payslip calculations under the 2026 Acts.

The code is organized by statutory provision (§§3–8).
§1 (definitions) and §2 (charge to tax) need no code. Gross pay is
the amount in the request, and every payslip charges and withholds
tax, so neither has any logic of its own.
The calculation itself follows the dependencies between those provisions.

Each computed amount carries the provisions that produced it. A line's
basis lists those provisions, separated by ", ", then ": " and a
plain-language explanation.
"""

import calendar
from datetime import date
from decimal import Decimal, ROUND_DOWN
from typing import NamedTuple

from .models import (
    FreedoniaLaw,
    PayslipLine,
    PayslipRequest,
    PayslipResponse,
    TaxBand,
)

ZERO = Decimal("0")

class _CitedAmount(NamedTuple):
    """One computed amount and the provisions that produced it."""

    amount: Decimal
    provisions: list[str]
    explanation: str

# ===========================================================================
# §3 — RESIDENT TAXABLE INCOME
# ===========================================================================

def _calculate_resident_taxable_income(
    gross_pay: Decimal,
    employee_pension: Decimal,
    places: int,
) -> Decimal:
    """Calculate resident taxable income.

    Taxable income is gross pay less the employee pension contribution
    deducted under §5.
    """
    taxable_income = gross_pay - employee_pension
    return _round_money(taxable_income, places)

# ===========================================================================
# §4 — RESIDENT PROGRESSIVE INCOME TAX
# ===========================================================================

def _calculate_progressive_tax(
    taxable_income: Decimal,
    law: FreedoniaLaw,
    places: int,
) -> tuple[Decimal, list[tuple[TaxBand, Decimal]]]:
    """Calculate tax using the resident progressive tax bands.

    Each band applies to the portion of taxable income falling within it.
    The tax amounts for all bands are added together before the total
    tax under §4 is rounded down under §8.

    Returns the rounded tax and each band that applied, with the income
    taxed in it.
    """
    remaining_income = taxable_income
    total_tax = ZERO
    bands_used: list[tuple[TaxBand, Decimal]] = []

    # we assume the tax bands will be structured like limits rather than ranges
    # or, if structured like ranges, that the range is continuous so that they can be
    # represented as limits
    for band in law.resident.tax.bands:
        if remaining_income <= ZERO:
            break

        if band.limit is None:
            income_in_band = remaining_income
        else:
            income_in_band = min(remaining_income, band.limit)

        total_tax += income_in_band * band.rate
        remaining_income -= income_in_band
        bands_used.append((band, income_in_band))

    # Round the total tax calculated under §4, not each band separately.
    return _round_money(total_tax, places), bands_used

# ===========================================================================
# §5 — PENSION CONTRIBUTIONS
# ===========================================================================

def _attained_age_before_payment_month(
    date_of_birth: date,
    age: int,
    pay_date: date,
) -> bool:
    """Check §5(3): the employee attained ``age`` before the first day of the payment month.

    The Act does not specify how to treat February 29 birthdays in
    non-leap years. This implementation treats February 28 as the
    birthday in those years.
    """
    year = date_of_birth.year + age
    is_leap_day = (date_of_birth.month, date_of_birth.day) == (2, 29)
    birthday = (
        (year, 2, 28)
        if is_leap_day and not calendar.isleap(year)
        else (year, date_of_birth.month, date_of_birth.day)
    )
    # compare with tuples instead of dates because the birthday can be after max date
    return birthday < (pay_date.year, pay_date.month, 1)

def _calculate_pension_contributions(
    request: PayslipRequest,
    gross_pay: Decimal,
    law: FreedoniaLaw,
    places: int,
) -> tuple[_CitedAmount, _CitedAmount]:
    """Return the employee and employer §5 contributions."""
    pension = law.resident.pension
    gross = _format_money(gross_pay, places)
    cap = f"{law.currency} {_format_money(pension.cap, places)}"

    # §5(1): each contributes the rate times the employee's gross pay.
    employee_contribution = gross_pay * pension.employee_rate
    employer_contribution = gross_pay * pension.employer_rate
    employee_provisions = [pension.rate_provision]
    employer_provisions = [pension.rate_provision]
    employee_text = (
        f"employee contribution of {_format_rate(pension.employee_rate)} "
        f"of gross pay {gross}"
    )
    employer_text = (
        f"employer contribution of {_format_rate(pension.employer_rate)} "
        f"of gross pay {gross}"
    )

    # §5(2): neither contribution exceeds the cap.
    if employee_contribution > pension.cap:
        employee_contribution = pension.cap
        employee_provisions.append(pension.cap_provision)
        employee_text += f", capped at {cap} per payment"
    if employer_contribution > pension.cap:
        employer_contribution = pension.cap
        employer_provisions.append(pension.cap_provision)
        employer_text += f", capped at {cap} per payment"

    # §5(3): an employee past the exemption age does not contribute;
    # the employer's contribution remains payable.
    if _attained_age_before_payment_month(
        request.employee.date_of_birth, pension.age_exemption, request.pay_date
    ):
        employee_contribution = ZERO
        employee_provisions = [pension.exemption_provision]
        employee_text = (
            f"employee attained age {pension.age_exemption} before "
            f"{request.pay_date.replace(day=1).isoformat()}, the first day of "
            f"the payment month, so no employee contribution is required"
        )
        # The employer basis does not cite §5(3) (because it is just an exclusion
        # that does NOT apply), but we add a short explanation note
        employer_text += "; still payable although the employee is exempt"

    return (
        _CitedAmount(
            _round_money(employee_contribution, places), employee_provisions, f"{employee_text}."
        ),
        _CitedAmount(
            _round_money(employer_contribution, places), employer_provisions, f"{employer_text}."
        ),
    )

# ===========================================================================
# §6 — RESIDENT PERSONAL RELIEF
# ===========================================================================

def _apply_personal_relief(
    tax_under_section_4: Decimal,
    law: FreedoniaLaw,
    places: int,
) -> tuple[Decimal, Decimal]:
    """Reduce the resident's §4 tax by the personal relief, but not below zero.

    Returns the tax payable and the part of the relief that was used.
    """
    relief_used = min(law.resident.tax.personal_relief, tax_under_section_4)
    tax_payable = _round_money(tax_under_section_4 - relief_used, places)
    return tax_payable, relief_used

# ===========================================================================
# §7 — NON-RESIDENTS
# ===========================================================================

def _calculate_non_resident(
    gross_pay: Decimal,
    law: FreedoniaLaw,
    places: int,
) -> tuple[_CitedAmount, _CitedAmount, _CitedAmount]:
    """Return the employee pension, income tax and employer pension.

    §7(a) disapplies §§3–6, including both pension contributions.
    §7(b) charges tax at a flat rate on gross pay.
    """
    non_resident = law.non_resident
    tax = _round_money(gross_pay * non_resident.tax_rate, places)

    income_tax = _CitedAmount(
        tax,
        [
            law.charge_provision,
            non_resident.exclusion_provision,
            non_resident.tax_provision,
        ],
        f"non-resident, so the resident rules do not apply; flat tax of "
        f"{_format_rate(non_resident.tax_rate)} of gross pay "
        f"{_format_money(gross_pay, places)}; the employer withholds "
        f"{_format_money(tax, places)}.",
    )
    no_pension = _CitedAmount(
        ZERO,
        [non_resident.exclusion_provision],
        "non-resident, so no pension contribution is payable.",
    )
    # specifically in order PENSION_EE, INCOME_TAX, PENSION_ER
    return no_pension, income_tax, no_pension

# ===========================================================================
# §8 — ROUNDING
# ===========================================================================

def _round_money(value: Decimal, places: int) -> Decimal:
    """Round a computed amount down to the required decimal places."""
    quantum = Decimal(1).scaleb(-places)
    return value.quantize(quantum, rounding=ROUND_DOWN)


def _format_money(value: Decimal, places: int) -> str:
    """Format a monetary amount to the required decimal places."""
    return f"{_round_money(value, places):.{places}f}"


def _format_rate(rate: Decimal) -> str:
    """Format a rate such as 0.275 as "27.5%"."""
    return f"{(rate * 100).normalize():f}%"

# ===========================================================================
# COMPLETE PAYSLIP CALCULATION
# ===========================================================================

def _calculate_resident_tax(
    gross_pay: Decimal,
    employee_pension: Decimal,
    law: FreedoniaLaw,
    places: int,
) -> _CitedAmount:
    """Calculate the tax charged under §2 and withheld by the employer."""
    taxable_income = _calculate_resident_taxable_income(
        gross_pay, employee_pension, places
    )
    tax_under_section_4, bands_used = _calculate_progressive_tax(
        taxable_income, law, places
    )
    income_tax, relief_used = _apply_personal_relief(
        tax_under_section_4, law, places
    )

    provisions = [law.charge_provision, law.resident.taxable_income_provision]
    explanation_parts = [
        f"taxable income {_format_money(taxable_income, places)} is gross pay "
        f"less the employee pension contribution"
    ]

    if bands_used:
        provisions.extend(band.provision for band, _ in bands_used)
        band_text = ", ".join(
            f"{_format_money(income, places)} at {_format_rate(band.rate)}"
            for band, income in bands_used
        )
        explanation_parts.append(
            f"tax of {_format_money(tax_under_section_4, places)} on {band_text}"
        )

    if relief_used > ZERO:
        provisions.append(law.resident.tax.relief_provision)
        personal_relief = law.resident.tax.personal_relief
        relief_text = (
            f"less personal relief of {law.currency} "
            f"{_format_money(personal_relief, places)}"
        )
        # Only mention the floor when it actually limited the relief.
        if relief_used < personal_relief:
            relief_text += ", but not below zero"
        explanation_parts.append(relief_text)

    explanation_parts.append(f"the employer withholds {_format_money(income_tax, places)}")
    return _CitedAmount(income_tax, provisions, "; ".join(explanation_parts) + ".")

def _calculate_resident(
    request: PayslipRequest,
    gross_pay: Decimal,
    law: FreedoniaLaw,
    places: int,
) -> tuple[_CitedAmount, _CitedAmount, _CitedAmount]:
    """Return the employee pension, income tax and employer pension."""
    # §3 deducts the employee contribution, so §5 must be calculated first.
    employee_pension, employer_pension = _calculate_pension_contributions(
        request, gross_pay, law, places
    )
    income_tax = _calculate_resident_tax(
        gross_pay, employee_pension.amount, law, places
    )
    return employee_pension, income_tax, employer_pension

def _make_line(code: str, party: str, item: _CitedAmount, places: int) -> PayslipLine:
    return PayslipLine(
        code=code,
        party=party,
        amount=_format_money(item.amount, places),
        basis=f"{', '.join(item.provisions)}: {item.explanation}",
    )

class PayslipCalculator:
    """Calculate a payslip under the applicable version of Freedonia law."""

    def calculate(
        self,
        request: PayslipRequest,
        law: FreedoniaLaw,
    ) -> PayslipResponse:
        places = law.rounding.decimal_places
        gross_pay = request.gross_pay

        if request.employee.resident:
            employee_pension, income_tax, employer_pension = _calculate_resident(
                request, gross_pay, law, places
            )
        else:
            employee_pension, income_tax, employer_pension = (
                _calculate_non_resident(gross_pay, law, places)
            )

        # The employer contribution is not deducted from the employee's pay.
        net_pay = gross_pay - employee_pension.amount - income_tax.amount

        return PayslipResponse(
            employee_id=request.employee.id,
            gross_pay=_format_money(gross_pay, places),
            lines=[
                _make_line("PENSION_EE", "employee", employee_pension, places),
                _make_line("INCOME_TAX", "employee", income_tax, places),
                _make_line("PENSION_ER", "employer", employer_pension, places),
            ],
            net_pay=_format_money(net_pay, places),
            law_version=law.version,
        )
