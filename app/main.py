"""FastAPI application exposing POST /payslips/calculate."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .law_repository import LawRepository, NoApplicableLawError
from .models import PayslipRequest
from .payslip_calculator import PayslipCalculator

app = FastAPI(title="Freedonia Payroll Service")

law_repository = LawRepository()
law_repository.load_all()
calculator = PayslipCalculator()


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(
    request: Request, exc: RequestValidationError
):
    details = "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
        for error in exc.errors()
    )
    return JSONResponse(status_code=400, content={"error": details})


@app.exception_handler(NoApplicableLawError)
async def no_applicable_law_handler(request: Request, exc: NoApplicableLawError):
    return JSONResponse(status_code=400, content={"error": f"pay_date: {exc}"})


@app.post("/payslips/calculate")
def calculate_payslip(request: PayslipRequest):
    law = law_repository.get_law(request.pay_date)
    return calculator.calculate(request, law)
