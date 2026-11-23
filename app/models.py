"""Validated request, law-configuration, and response models."""

import re
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

GROSS_PAY_PATTERN = re.compile(r"[0-9]+(\.[0-9]{1,2})?")
# Years 0000 to 9999 accepted from pattern alone
PAY_PERIOD_PATTERN = re.compile(r"[0-9]{4}-(0[1-9]|1[0-2])")

# Keeps gross pay times any rate exact within Decimal's default 28 digits.
MAX_GROSS_PAY = Decimal("999999999999.99")


class EmployeeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    resident: bool
    date_of_birth: date

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("employee id must not be blank")
        return value


class PayslipRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee: EmployeeInput
    gross_pay: Decimal
    pay_period: str
    pay_date: date

    @field_validator("gross_pay", mode="before")
    @classmethod
    def validate_gross_pay(cls, value: object) -> Decimal:
        # Reject JSON numbers: they are parsed as floats and may have lost cents.
        # we expect the pay to come in as a string
        if not isinstance(value, str) or not GROSS_PAY_PATTERN.fullmatch(value):
            raise ValueError(
                "gross_pay must be a string holding a non-negative amount "
                'with at most 2 decimal places, such as "30000.00"'
            )
        amount = Decimal(value)
        if amount > MAX_GROSS_PAY:
            raise ValueError(f"gross_pay cannot exceed {MAX_GROSS_PAY}")
        return amount

    @field_validator("pay_period")
    @classmethod
    def validate_pay_period(cls, value: str) -> str:
        if not PAY_PERIOD_PATTERN.fullmatch(value):
            raise ValueError("pay_period must use YYYY-MM format")
        return value

    @model_validator(mode="after")
    def validate_dates(self):
        if self.employee.date_of_birth > self.pay_date:
            raise ValueError("date_of_birth cannot be after pay_date")
        return self


class TaxBand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: Decimal | None
    rate: Decimal
    provision: str


class TaxRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bands: list[TaxBand]
    personal_relief: Decimal
    relief_provision: str


class PensionRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_rate: Decimal
    employer_rate: Decimal
    rate_provision: str
    cap: Decimal
    cap_provision: str
    age_exemption: int
    exemption_provision: str


class ResidentRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    taxable_income_provision: str
    tax: TaxRules
    pension: PensionRules


class NonResidentRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exclusion_provision: str
    tax_rate: Decimal
    tax_provision: str


class RoundingRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decimal_places: int


def _version_name(effective_from: date) -> str:
    """Return a version name formed from its start date, e.g. "2026.7.1"."""
    return f"{effective_from.year}.{effective_from.month}.{effective_from.day}"


class FreedoniaLaw(BaseModel):
    model_config = ConfigDict(extra="forbid")

    effective_from: date
    currency: str
    charge_provision: str
    resident: ResidentRules
    non_resident: NonResidentRules
    rounding: RoundingRules

    @property
    def version(self) -> str:
        return _version_name(self.effective_from)

    @model_validator(mode="after")
    def validate_law(self):
        if not self.currency.strip():
            raise ValueError("law currency is required")
        if self.rounding.decimal_places < 0:
            raise ValueError("rounding decimal_places cannot be negative")

        def check_rate(rate: Decimal, description: str):
            if not rate.is_finite() or rate < 0 or rate > 1:
                raise ValueError(f"{description} must be between 0 and 1")

        # A payslip basis lists provisions separated by ", " and ends the list with ": ".
        def check_provision(provision: str, description: str):
            if not provision.strip():
                raise ValueError(f"{description} must identify its provision")
            if ", " in provision or ": " in provision:
                raise ValueError(
                    f"{description} provision cannot contain ', ' or ': '"
                )

        tax = self.resident.tax
        pension = self.resident.pension

        if not tax.bands:
            raise ValueError("at least one resident tax band is required")
        if tax.personal_relief < 0:
            raise ValueError("personal relief cannot be negative")
        if pension.cap < 0:
            raise ValueError("pension cap cannot be negative")
        if pension.age_exemption < 0:
            raise ValueError("pension age exemption cannot be negative")

        for name, rate in (
            ("employee pension rate", pension.employee_rate),
            ("employer pension rate", pension.employer_rate),
            ("non-resident tax rate", self.non_resident.tax_rate),
        ):
            check_rate(rate, name)

        for index, band in enumerate(tax.bands):
            is_last = index == len(tax.bands) - 1
            check_rate(band.rate, "tax band rate")
            if band.limit is None and not is_last:
                raise ValueError("only the final tax band may be unlimited")
            if band.limit is not None and band.limit <= 0:
                raise ValueError("tax band limits must be positive")
            check_provision(band.provision, "every tax band")
        # This is a legal assumption that there WILL be a final tax band
        # This is a problem if, for example, there is NO final tax band at all because it would
        # have no provision to point to for its tax rate of 0%
        if tax.bands[-1].limit is not None:
            raise ValueError("the final tax band must be unlimited")

        for name, provision in (
            ("the charge to tax", self.charge_provision),
            ("resident taxable income", self.resident.taxable_income_provision),
            ("personal relief", tax.relief_provision),
            ("the pension rate", pension.rate_provision),
            ("the pension cap", pension.cap_provision),
            ("the pension age exemption", pension.exemption_provision),
            ("the non-resident exclusion", self.non_resident.exclusion_provision),
            ("the non-resident tax rate", self.non_resident.tax_provision),
        ):
            check_provision(provision, name)
        return self


class LawAmendment(BaseModel):
    """A law version given as the values it changes in the version before it."""

    model_config = ConfigDict(extra="forbid")

    effective_from: date
    # Dotted paths into the law, with list items by index, e.g. "resident.tax.bands.1.rate".
    changes: dict[str, str]

    @model_validator(mode="after")
    def validate_changes(self):
        if not self.changes:
            raise ValueError("an amendment must change at least one value")
        if "effective_from" in self.changes:
            raise ValueError("amendment changes cannot change effective_from")
        return self


class PayslipLine(BaseModel):
    code: str
    party: Literal["employee", "employer"]
    amount: str
    basis: str


class PayslipResponse(BaseModel):
    employee_id: str
    gross_pay: str
    lines: list[PayslipLine]
    net_pay: str
    law_version: str
