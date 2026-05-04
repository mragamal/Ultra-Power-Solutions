from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

router = APIRouter()


@router.get("/ui/accounting/petty-cash/fund")
def fund_page(request: Request, employee_id: int = 0):
    target = "/ui/accounting/petty-cash/custody"
    return RedirectResponse(url=target, status_code=302)


@router.post("/ui/accounting/petty-cash/fund")
def save_fund():
    target = "/ui/accounting/petty-cash/custody"
    return RedirectResponse(url=target, status_code=302)