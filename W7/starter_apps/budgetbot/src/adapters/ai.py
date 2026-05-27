"""AI adapters. BudgetBot uses direct InvokeModel — no KB / no RAG.

Interface:
    categorize(description, amount, date) -> {
        "category": str,
        "confidence": "high|medium|low",
        "confidence_score": float,
        "needs_review": bool,
        "reason": str,
        "suggested_categories": list[str],
        "classifier": "local-rules|bedrock-few-shot"
    }
"""
import json
import re
from typing import Any


CATEGORIES = [
    "Food", "Transport", "Shopping", "Utilities", "Entertainment",
    "Health", "Subscriptions", "Income", "Transfer", "Other",
]

# ĐÃ SỬA: thêm mapping confidence dạng chữ sang điểm số để đáp ứng yêu cầu Domain 2
# về confidence scoring và review queue.
CONFIDENCE_SCORES = {
    "high": 0.90,
    "medium": 0.70,
    "low": 0.40,
}
REVIEW_THRESHOLD = 0.60


# ĐÃ SỬA: prompt chuyển từ zero-shot đơn giản sang few-shot prompt.
# Lý do: Domain 2 yêu cầu xử lý giao dịch mơ hồ như GRAB, VINMART, FT code.
CATEGORIZE_PROMPT = """You are a personal finance transaction classifier for Vietnamese bank statements.

Choose exactly one category from:
{categories}

Rules:
- Return JSON only. No markdown.
- Positive amount is usually Income or Transfer.
- If the description is an opaque bank code, use Other with low confidence.
- GRAB without FOOD is usually Transport, but confidence should be medium if ambiguous.
- GrabFood/ShopeeFood are Food.
- Shopee/Lazada/Tiki are Shopping unless clearly food.
- Subscriptions include Netflix, Spotify, YouTube Premium, iCloud, Notion, Cursor, GitHub, OpenAI.
- Low confidence means the user should review the transaction.

Examples:
Transaction: "Highlands Coffee - Bui Vien", Amount: -65000
{{"category":"Food","confidence":"high","reason":"Coffee merchant indicates food and beverage.","suggested_categories":["Food"]}}

Transaction: "T1908 GRAB CITY", Amount: -95000
{{"category":"Transport","confidence":"medium","reason":"GRAB CITY likely ride transport, but could be ambiguous.","suggested_categories":["Transport","Food"]}}

Transaction: "GrabFood Nguyen Trai", Amount: -120000
{{"category":"Food","confidence":"high","reason":"GrabFood indicates food delivery.","suggested_categories":["Food"]}}

Transaction: "FT0024112501 ID:0001", Amount: -500000
{{"category":"Other","confidence":"low","reason":"Opaque bank transfer code has no merchant context.","suggested_categories":["Other","Transfer"]}}

Transaction: "Netflix monthly subscription", Amount: -260000
{{"category":"Subscriptions","confidence":"high","reason":"Known recurring subscription merchant.","suggested_categories":["Subscriptions"]}}

Transaction: "Salary deposit payroll", Amount: 18500000
{{"category":"Income","confidence":"high","reason":"Salary/payroll deposit is income.","suggested_categories":["Income"]}}

Now classify this transaction:
Description: "{description}"
Amount: {amount}
Date: {date}

Return JSON with this exact shape:
{{"category":"<category>","confidence":"high|medium|low","reason":"<short reason>","suggested_categories":["<category>"]}}"""


def _normalize_confidence(value: Any) -> str:
    value = str(value or "medium").strip().lower()
    if value in CONFIDENCE_SCORES:
        return value
    return "medium"


def _finalize_result(obj: dict, classifier: str) -> dict:
    """ĐÃ SỬA: chuẩn hóa kết quả AI/local về cùng format để frontend dễ hiển thị."""
    category = obj.get("category") if obj.get("category") in CATEGORIES else "Other"
    confidence = _normalize_confidence(obj.get("confidence"))
    score = float(obj.get("confidence_score", CONFIDENCE_SCORES[confidence]))
    suggested = obj.get("suggested_categories") or [category]
    if isinstance(suggested, str):
        suggested = [suggested]
    suggested = [c for c in suggested if c in CATEGORIES] or [category]

    return {
        "category": category,
        "confidence": confidence,
        "confidence_score": score,
        "needs_review": bool(obj.get("needs_review", score < REVIEW_THRESHOLD or category == "Other")),
        "reason": obj.get("reason") or "No detailed reason provided.",
        "suggested_categories": suggested,
        "classifier": classifier,
    }


def _parse_json_response(text: str) -> dict:
    """Extract first JSON object from LLM response. Falls back to Other if invalid."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n?|```$", "", text, flags=re.MULTILINE).strip()
    match = re.search(r"\{[^}]+\}", text, re.DOTALL)
    if match:
        try:
            obj = json.loads(match.group())
            return _finalize_result(obj, classifier="bedrock-few-shot")
        except json.JSONDecodeError:
            pass
    return _finalize_result({
        "category": "Other",
        "confidence": "low",
        "reason": "Model response was not valid JSON.",
        "suggested_categories": ["Other"],
    }, classifier="bedrock-few-shot")


class BedrockAI:
    def __init__(self, region: str, model_id: str):
        import boto3
        self.runtime = boto3.client("bedrock-runtime", region_name=region)
        self.model_id = model_id

    def categorize(self, description: str, amount: float, date: str) -> dict:
        prompt = CATEGORIZE_PROMPT.format(
            categories=", ".join(CATEGORIES),
            description=description,
            amount=amount,
            date=date,
        )
        resp = self.runtime.converse(
            modelId=self.model_id,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 180, "temperature": 0.0},
        )
        text = resp["output"]["message"]["content"][0]["text"]
        return _parse_json_response(text)


class LocalAI:
    """Rule-based categorizer. Keyword matching only. Use for development."""

    # ĐÃ SỬA: local rules được mở rộng để mô phỏng hybrid classifier:
    # rule rõ ràng -> high confidence, rule mơ hồ -> medium/low và needs_review.
    KEYWORDS = {
        "Income": ["salary", "deposit credit", "payroll", "bonus", "wage"],
        "Transfer": [
            "transfer to", "transfer from", "moved to savings", "cash withdrawal", "atm",
            "refund", "foreign exchange fee", "bank fee",
        ],
        "Subscriptions": [
            "subscription", "netflix", "spotify", "openai", "chatgpt", "anthropic",
            "claude", "github", "icloud", "google one", "youtube premium",
            "notion", "cursor pro", "fpt play",
        ],
        "Food": [
            "restaurant", "cafe", "coffee", "starbucks", "highlands", "phở", "pho",
            "food", "grabfood", "grab food", "shopeefood", "shopee food", "lunch",
            "dinner", "bakery", "kfc", "lotteria", "pizza", "banh mi", "phuc long",
            "coffee house", "co.opmart", "coopmart", "mega market", "vinmart",
        ],
        "Transport": [
            "grab car", "grab bike", "grab city", "uber", " be ", "be taxi",
            "xanh sm", "taxi", "metro", "bus", "petrol", "shell", "vinfast",
            "fuel", "vietnam airlines", "han-sgn",
        ],
        "Shopping": ["shopee", "lazada", "tiki", "amazon", "store", "mall", "vincom", "shop", "uniqlo", "macbook"],
        "Utilities": ["electric", "evn", "water", "sawaco", "internet", "viettel", "vnpt", "fpt", "utility", "fiber"],
        "Entertainment": ["cinema", "cgv", "lotte cinema", "concert", "game", "steam"],
        "Health": ["pharmacy", "pharmacity", "hospital", "clinic", "guardian", "long chau", "medlatec"],
    }

    AMBIGUOUS_PATTERNS = [
        ("grab", ["Transport", "Food"], "GRAB can be ride transport or food delivery."),
        ("vinmart", ["Food", "Shopping"], "VINMART may be groceries or household shopping."),
        ("unknown", ["Other"], "Unknown merchant requires manual review."),
        ("ft00", ["Other", "Transfer"], "Opaque bank transfer code has no merchant context."),
        ("id:000", ["Other", "Transfer"], "Opaque bank ID has no merchant context."),
    ]

    def categorize(self, description: str, amount: float, date: str) -> dict:
        desc_lower = f" {description.lower()} "

        for pattern, suggestions, reason in self.AMBIGUOUS_PATTERNS:
            if pattern in desc_lower:
                # GrabFood là Food rõ ràng nên không đưa vào review.
                if pattern == "grab" and ("grabfood" in desc_lower or "grab food" in desc_lower):
                    return _finalize_result({
                        "category": "Food",
                        "confidence": "high",
                        "reason": "GrabFood/Grab Food indicates food delivery.",
                        "suggested_categories": ["Food"],
                    }, classifier="local-rules")
                return _finalize_result({
                    "category": suggestions[0],
                    "confidence": "medium" if len(suggestions) > 1 else "low",
                    "reason": reason,
                    "suggested_categories": suggestions,
                    "needs_review": True,
                }, classifier="local-rules")

        for category, keywords in self.KEYWORDS.items():
            for kw in keywords:
                if kw in desc_lower:
                    return _finalize_result({
                        "category": category,
                        "confidence": "high",
                        "reason": f"Matched keyword '{kw.strip()}'.",
                        "suggested_categories": [category],
                    }, classifier="local-rules")

        try:
            if float(amount) > 0:
                return _finalize_result({
                    "category": "Income",
                    "confidence": "medium",
                    "reason": "Positive amount usually means income or incoming transfer.",
                    "suggested_categories": ["Income", "Transfer"],
                    "needs_review": True,
                }, classifier="local-rules")
        except (TypeError, ValueError):
            pass

        return _finalize_result({
            "category": "Other",
            "confidence": "low",
            "reason": "No reliable keyword matched.",
            "suggested_categories": ["Other"],
            "needs_review": True,
        }, classifier="local-rules")
