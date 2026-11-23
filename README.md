# Freedonia Payroll Service

A small HTTP service that calculates the deductions for one payment under the
Freedonia Income Tax and Pensions Act 2026, as amended by the Finance
(Amendment) Act 2026.

## Running the service and the tests

You need Python 3.10 or newer. From the project directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Start the service:

```bash
uvicorn app.main:app --reload
```

It listens on `http://127.0.0.1:8000`, and FastAPI serves interactive API docs
at `http://127.0.0.1:8000/docs`. To send a request:

```bash
curl -X POST http://127.0.0.1:8000/payslips/calculate \
  -H 'Content-Type: application/json' \
  -d '{
    "employee": {"id": "emp-001", "resident": true, "date_of_birth": "1988-03-14"},
    "gross_pay": "30000.00",
    "pay_period": "2026-06",
    "pay_date": "2026-07-02"
  }'
```

Run the tests:

```bash
python3 -m pytest
```

## Interpretations

- **When the original Act starts.** The Act gives no commencement date. 
  - **Decision:** I used January 1st, 2026. A payment dated before 2026 gets a `400` instead of being
  charged under a law that was not yet in force.
  - **Why:** The Act is named for 2026 and nothing points to a
  later start. 
- **Rounding each band or the §4 total.** §8 rounds "any amount computed under
  this Act". That is ambiguous as to the rounding of intermediate calculations. 
  - **Decision:** I read that as the amounts the Act names: taxable income, each
  pension contribution, the §4 tax, the tax after §6 relief, and the §7 flat
  tax. So the §4 tax is rounded once, after the bands are added, not band by
  band. This is the only rounding choice that can change a result, and only
  under the amended law once taxable income reaches the 30% band. For
  example, gross pay of FRD 33,413.09 paid after July 1st, 2026 gives tax of
  2,191.60. Rounding each band would give 2,191.59. 
  - **Why:** Tax forms commonly add the unrounded parts and round only the total. For example, the [IRS Schedule D instructions](https://www.irs.gov/instructions/i1040sd) say to "include cents when adding the amounts and round off only the total."
- **Birthdays on February 29th.** The Act does not say when someone born on
  February 29th turns 60 in a year without that date.
  - **Decision:** I used February 28th. The choice matters
  only for a payment made in March of a non-leap year in which the employee
  turns 60. With an exemption age of 60, the first such March is in 2100.
  - **Why:** There is no strong reason to choose either. Jurisdictions differ on this question. For example, New York uses February 28th ([General Construction Law §58](https://www.nysenate.gov/legislation/laws/GCN/58)),
  while Maine uses March 1st
  ([29-A M.R.S. §1406-A](https://legislature.maine.gov/statutes/29-A/title29-Asec1406-A.html)).
- **Bands and relief per payment.** §2 charges tax "in respect of each payment of gross pay". Read literally, the bands and the relief start again with every payment. So a resident paid in smaller blocks (FRD ~25k) can pay no income tax, because the relief cancels the tax on each block. That looks like a loophole the Act did not intend.
  - **Decision:** Each payment gets the full tax bands and the full personal relief, even when an employee is paid twice in one month.
  - **Why:** The Act has no rule for adding up income over a month or a year. Closing the loophole would mean inventing one. That is a policy problem, not a legal one.

## Design notes

### A second country
If the difference between the second country's law(s) and Freedonia's is effectively just different numbers on top of the existing structure, there are only a few places that need to be changed (the model, `get_law` method of `LawRepository`, API, and documentation).

If the difference is more extensive, the `PayslipCalculator` and the `FreedoniaLaw` model could not be reused. I would create abstract calculator and law classes for the country-specific ones to inherit from, and share anything more general (repository and law picking machinery, rounding, and response formatting).

### A retroactive amendment
I would add an `enacted_on` field to the law model separate from the `effective_from` date, and a date field on the API request and response to allow users to indicate what version of laws should be known to the model (`laws_as_known_on`). Then, the service could exclude any laws enacted after the processing date, allowing users to process payments as they would have been processed on any given day. The service stores no information, so it would be up to users how they approach adjusting their already-processed payments.

### Trade-off
I store each amendment as a patch: a file listing only the values it changes, mirroring the amending Act, which is itself written as changes ("for '25%' substitute '27.5%'"). Entering one never means retracing earlier amendments, and a retroactive amendment is one new file that carries into every later version, simplifying the work of a law-maintainer. The cost is auditability: the law in force on a given date exists only once the service builds it, so a reviewer cannot check it against a consolidated text by reading one file. In addition, a mistake in an early amendment or a wrongly dated amendment can lead to a chain of errors to every later version. I accepted these costs because tracking which amendments are in force is one of the hard parts of applying payroll law, and a person is far more likely to get that wrong than to record one amending Act correctly.
# Freedonia-Payroll-dxhw
