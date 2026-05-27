"""FastAPI app for BudgetBot. Runtime-agnostic."""
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.config import config
from src.adapters import factory
from src import handlers


app = FastAPI(title="BudgetBot — W7 Capstone Starter")


# CORS — allow frontend to live on a different origin (CloudFront / Amplify / separate ALB).
# CORS_ORIGINS env var controls this; default '*' is permissive for hackathon.
_allowed = ["*"] if config.cors_origins == "*" else [o.strip() for o in config.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ai_client = factory.make_ai()
storage = factory.make_storage()
userstore = factory.make_userstore()


# ĐÃ SỬA: thêm request models cho correction và budget cap.
class CorrectCategoryRequest(BaseModel):
    category: str


class BudgetCapRequest(BaseModel):
    month: str
    category: str
    cap_amount: float


def _resolve_user_id(x_user_id: Optional[str]) -> str:
    return x_user_id or config.default_user_id


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "backends": {
            "ai": config.ai_backend,
            "storage": config.storage_backend,
            "userstore": config.userstore_backend,
        },
        # ĐÃ SỬA: trả thêm capabilities để demo/evidence dễ kiểm tra.
        "capabilities": [
            "csv_upload",
            "transaction_classification",
            "confidence_scoring",
            "review_queue",
            "spending_summary",
            "budget_cap_alerts",
        ],
    }


@app.post("/upload")
async def upload(
    file: UploadFile = File(...),
    x_user_id: Optional[str] = Header(default=None),
) -> dict:
    user_id = _resolve_user_id(x_user_id)
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    return handlers.handle_upload(
        user_id=user_id,
        filename=file.filename or "statement.csv",
        data=data,
        ai_client=ai_client,
        storage=storage,
        userstore=userstore,
    )


@app.get("/summary")
def summary(
    month: Optional[str] = None,
    x_user_id: Optional[str] = Header(default=None),
) -> dict:
    """`month` format: YYYY-MM. Omit for all-time summary."""
    return handlers.handle_summary(_resolve_user_id(x_user_id), month, userstore)


@app.get("/transactions")
def transactions(
    month: Optional[str] = None,
    x_user_id: Optional[str] = Header(default=None),
) -> dict:
    return handlers.handle_list_transactions(_resolve_user_id(x_user_id), month, userstore)


# ĐÃ SỬA: thêm API review queue cho low-confidence/ambiguous transactions.
@app.get("/review")
def review(
    month: Optional[str] = None,
    x_user_id: Optional[str] = Header(default=None),
) -> dict:
    return handlers.handle_review(_resolve_user_id(x_user_id), month, userstore)


# ĐÃ SỬA: thêm API cho user correction để chứng minh ambiguity handling/user-correctable category.
@app.post("/transactions/{transaction_id}/correct")
def correct_transaction(
    transaction_id: str,
    payload: CorrectCategoryRequest,
    x_user_id: Optional[str] = Header(default=None),
) -> dict:
    return handlers.handle_correct_transaction(
        _resolve_user_id(x_user_id),
        transaction_id,
        payload.category,
        userstore,
    )


# ĐÃ SỬA: thêm API set monthly cap cho category.
@app.post("/budget/cap")
def set_budget_cap(
    payload: BudgetCapRequest,
    x_user_id: Optional[str] = Header(default=None),
) -> dict:
    return handlers.handle_set_budget_cap(_resolve_user_id(x_user_id), payload.model_dump(), userstore)


# ĐÃ SỬA: thêm API budget alerts để demo category cap bị vượt.
@app.get("/budget/alerts")
def budget_alerts(
    month: Optional[str] = None,
    x_user_id: Optional[str] = Header(default=None),
) -> dict:
    return handlers.handle_budget_alerts(_resolve_user_id(x_user_id), month, userstore)


# ---- Static frontend ----
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


if config.serve_frontend:
    @app.get("/")
    def index() -> FileResponse:
        """Convenience: serves frontend/index.html at /. Set SERVE_FRONTEND=false
        if you deploy the frontend separately (CloudFront+S3, Amplify, ALB)."""
        return FileResponse(FRONTEND_DIR / "index.html")
