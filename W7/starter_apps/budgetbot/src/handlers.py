"""Endpoint business logic for BudgetBot."""
import csv
import io
from typing import Optional


# ĐÃ SỬA: giới hạn số dòng upload để demo local/AWS an toàn hơn,
# tránh gọi Bedrock quá nhiều lần khi CSV lớn.
MAX_ROWS_PER_UPLOAD = 80


def _parse_csv(data: bytes) -> list:
    """Expect CSV columns: date, description, amount. Header row optional."""
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return []
    header = [c.lower().strip() for c in rows[0]]
    if "date" in header and "amount" in header:
        idx = {col: i for i, col in enumerate(header)}
        data_rows = rows[1:]
    else:
        idx = {"date": 0, "description": 1, "amount": 2}
        data_rows = rows
    parsed = []
    for r in data_rows[:MAX_ROWS_PER_UPLOAD]:
        if len(r) < 3 or not r[idx.get("date", 0)].strip():
            continue
        try:
            parsed.append({
                "date": r[idx.get("date", 0)].strip(),
                "description": r[idx.get("description", 1)].strip(),
                "amount": float(r[idx.get("amount", 2)].strip().replace(",", "")),
            })
        except (ValueError, IndexError):
            continue
    return parsed


def _month_from_date(date_value: str) -> str:
    """ĐÃ SỬA: thêm month field để summary/filter theo tháng dễ hơn."""
    return (date_value or "")[:7]


def handle_upload(
    user_id: str,
    filename: str,
    data: bytes,
    ai_client,
    storage,
    userstore,
) -> dict:
    """Parse CSV → categorize each row via AI → persist to userstore."""
    key = f"{user_id}/{filename}"
    location = storage.put(key, data)
    rows = _parse_csv(data)
    inserted = 0
    low_confidence_count = 0
    review_count = 0
    samples = []

    for row in rows:
        cat_result = ai_client.categorize(
            description=row["description"], amount=row["amount"], date=row["date"]
        )
        # ĐÃ SỬA: lưu thêm confidence_score, needs_review, reason,
        # suggested_categories để chứng minh ambiguity handling trong Domain 2.
        txn = {
            "date": row["date"],
            "month": _month_from_date(row["date"]),
            "description": row["description"],
            "amount": row["amount"],
            "category": cat_result["category"],
            "confidence": cat_result.get("confidence", "medium"),
            "confidence_score": float(cat_result.get("confidence_score", 0.7)),
            "needs_review": bool(cat_result.get("needs_review", False)),
            "reason": cat_result.get("reason", ""),
            "suggested_categories": cat_result.get("suggested_categories", [cat_result["category"]]),
            "classifier": cat_result.get("classifier", "unknown"),
            "source_file": filename,
        }
        userstore.add_transaction(user_id, txn)
        inserted += 1
        if txn["confidence"] == "low" or txn["confidence_score"] < 0.6:
            low_confidence_count += 1
        if txn["needs_review"]:
            review_count += 1
        if len(samples) < 5:
            samples.append(txn)

    # ĐÃ SỬA: lưu metadata upload nếu store hỗ trợ để có evidence tốt hơn.
    if hasattr(userstore, "add_upload"):
        userstore.add_upload(user_id, {
            "filename": filename,
            "stored_at": location,
            "rows_parsed": len(rows),
            "rows_inserted": inserted,
            "low_confidence_count": low_confidence_count,
            "review_count": review_count,
        })

    return {
        "filename": filename,
        "stored_at": location,
        "rows_parsed": len(rows),
        "rows_inserted": inserted,
        "low_confidence_count": low_confidence_count,
        "review_count": review_count,
        "sample_categorized": samples,
        "note": "Rows with needs_review=true should be checked by a human before final budget decisions.",
    }


def handle_summary(user_id: str, month: Optional[str], userstore) -> dict:
    summary = userstore.summary(user_id, month=month)
    total = sum(v["total"] for v in summary.values())
    sorted_cats = sorted(summary.items(), key=lambda kv: -abs(kv[1]["total"]))
    return {
        "user_id": user_id,
        "month": month,
        "total_spend": total,
        "by_category": dict(sorted_cats),
        "top_3_drivers": [
            {"category": cat, "total": v["total"], "count": v["count"]}
            for cat, v in sorted_cats[:3]
        ],
    }


def handle_list_transactions(user_id: str, month: Optional[str], userstore) -> dict:
    return {"user_id": user_id, "month": month, "transactions": userstore.list_transactions(user_id, month=month)}


# ĐÃ SỬA: thêm review queue cho các giao dịch confidence thấp/mơ hồ.
def handle_review(user_id: str, month: Optional[str], userstore) -> dict:
    txns = userstore.list_transactions(user_id, month=month)
    review_items = [t for t in txns if t.get("needs_review") or float(t.get("confidence_score", 1.0)) < 0.6]
    return {
        "user_id": user_id,
        "month": month,
        "count": len(review_items),
        "transactions": review_items,
    }


# ĐÃ SỬA: thêm correction để user sửa category, phục vụ ambiguity handling/user-correctable categories.
def handle_correct_transaction(user_id: str, transaction_id: str, category: str, userstore) -> dict:
    if not hasattr(userstore, "correct_transaction"):
        return {"updated": False, "reason": "Current userstore does not support corrections."}
    return userstore.correct_transaction(user_id, transaction_id, category)


# ĐÃ SỬA: thêm budget cap để chứng minh user story monthly cap.
def handle_set_budget_cap(user_id: str, payload: dict, userstore) -> dict:
    month = payload.get("month")
    category = payload.get("category")
    cap_amount = float(payload.get("cap_amount", 0))
    if not month or not category or cap_amount <= 0:
        return {"saved": False, "reason": "month, category, and positive cap_amount are required"}
    if not hasattr(userstore, "set_budget_cap"):
        return {"saved": False, "reason": "Current userstore does not support budget caps."}
    userstore.set_budget_cap(user_id, month, category, cap_amount)
    return {"saved": True, "month": month, "category": category, "cap_amount": cap_amount}


# ĐÃ SỬA: alert được tính từ summary hiện tại so với cap đã lưu.
def handle_budget_alerts(user_id: str, month: Optional[str], userstore) -> dict:
    if not month:
        return {"user_id": user_id, "month": month, "alerts": [], "reason": "month is required, e.g. 2026-05"}
    if not hasattr(userstore, "list_budget_caps"):
        return {"user_id": user_id, "month": month, "alerts": [], "reason": "Current userstore does not support budget caps."}

    caps = userstore.list_budget_caps(user_id, month)
    summary = userstore.summary(user_id, month=month)
    alerts = []
    for cap in caps:
        category = cap["category"]
        cap_amount = float(cap["cap_amount"])
        actual_spend = abs(float(summary.get(category, {}).get("total", 0)))
        exceeded_by = actual_spend - cap_amount
        alerts.append({
            "category": category,
            "cap_amount": cap_amount,
            "actual_spend": actual_spend,
            "status": "exceeded" if exceeded_by > 0 else "ok",
            "exceeded_by": max(exceeded_by, 0),
        })
    return {"user_id": user_id, "month": month, "alerts": alerts}
