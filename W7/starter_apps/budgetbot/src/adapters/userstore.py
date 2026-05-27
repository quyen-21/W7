"""Transaction store adapters.

Interface:
    add_transaction(user_id, txn) -> None
    list_transactions(user_id, month=None) -> list[dict]
    summary(user_id, month=None) -> {category: {"total": float, "count": int}}

ĐÃ SỬA: mở rộng interface cho Domain 2:
    add_upload(user_id, upload) -> None
    correct_transaction(user_id, transaction_id, category) -> dict
    list_review_transactions(user_id, month=None) -> list[dict]
    set_budget_cap(user_id, month, category, cap_amount) -> None
    list_budget_caps(user_id, month) -> list[dict]
"""
import json
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _txn_id() -> str:
    return uuid.uuid4().hex[:12]


def _json_dumps(value) -> str:
    return json.dumps(value or [], ensure_ascii=False)


def _json_loads(value):
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        return json.loads(value)
    except Exception:
        return []


def _to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).lower() in {"1", "true", "yes"}


class DynamoDBUserStore:
    """PK=user_id, SK=TXN#<month>#<date>#<id>. Aggregation done in app for hackathon scale."""

    def __init__(self, table_name: str, region: str):
        import boto3
        if not table_name:
            raise ValueError("USERSTORE_TABLE must be set for DynamoDB backend")
        self.table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    def add_transaction(self, user_id: str, txn: dict) -> None:
        from decimal import Decimal
        txid = txn.get("transaction_id") or _txn_id()
        month = txn.get("month") or str(txn.get("date", ""))[:7]
        sk = f"TXN#{month}#{txn['date']}#{txid}"
        # ĐÃ SỬA: DynamoDB không nhận float, nên convert sang Decimal và lưu đủ field AI mới.
        item = {
            **txn,
            "transaction_id": txid,
            "month": month,
            "amount": Decimal(str(txn.get("amount", 0))),
            "confidence_score": Decimal(str(txn.get("confidence_score", 0.7))),
            "needs_review": bool(txn.get("needs_review", False)),
            "suggested_categories": txn.get("suggested_categories", []),
        }
        self.table.put_item(Item={"user_id": user_id, "sk": sk, "created_at": _now(), **item})

    def add_upload(self, user_id: str, upload: dict) -> None:
        upload_id = _txn_id()
        sk = f"UPLOAD#{_now()}#{upload_id}"
        self.table.put_item(Item={"user_id": user_id, "sk": sk, "created_at": _now(), **upload})

    def list_transactions(self, user_id: str, month: str | None = None) -> list:
        kwargs = {
            "KeyConditionExpression": "user_id = :u AND begins_with(sk, :p)",
            "ExpressionAttributeValues": {":u": user_id, ":p": f"TXN#{month}" if month else "TXN#"},
        }
        resp = self.table.query(**kwargs)
        return [_decimal_to_float(item) for item in resp.get("Items", [])]

    def summary(self, user_id: str, month: str | None = None) -> dict:
        return _aggregate(self.list_transactions(user_id, month))

    def correct_transaction(self, user_id: str, transaction_id: str, category: str) -> dict:
        txns = self.list_transactions(user_id)
        target = next((t for t in txns if t.get("transaction_id") == transaction_id), None)
        if not target:
            return {"updated": False, "reason": "Transaction not found"}
        old = target.get("category")
        self.table.update_item(
            Key={"user_id": user_id, "sk": target["sk"]},
            UpdateExpression="SET category = :c, corrected = :t, needs_review = :f, corrected_at = :now",
            ExpressionAttributeValues={":c": category, ":t": True, ":f": False, ":now": _now()},
        )
        return {"updated": True, "transaction_id": transaction_id, "old_category": old, "new_category": category}

    def set_budget_cap(self, user_id: str, month: str, category: str, cap_amount: float) -> None:
        from decimal import Decimal
        sk = f"CAP#{month}#{category}"
        self.table.put_item(Item={
            "user_id": user_id,
            "sk": sk,
            "month": month,
            "category": category,
            "cap_amount": Decimal(str(cap_amount)),
            "created_at": _now(),
        })

    def list_budget_caps(self, user_id: str, month: str) -> list:
        resp = self.table.query(
            KeyConditionExpression="user_id = :u AND begins_with(sk, :p)",
            ExpressionAttributeValues={":u": user_id, ":p": f"CAP#{month}"},
        )
        return [_decimal_to_float(item) for item in resp.get("Items", [])]


def _decimal_to_float(item: dict) -> dict:
    from decimal import Decimal
    return {k: (float(v) if isinstance(v, Decimal) else v) for k, v in item.items()}


class SQLiteUserStore:
    def __init__(self, db_path: str):
        import sqlite3
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        # ĐÃ SỬA: schema SQLite thêm confidence_score, needs_review, reason,
        # suggested_categories, classifier, source_file và budget_caps.
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                transaction_id TEXT UNIQUE,
                user_id TEXT NOT NULL,
                txn_date TEXT NOT NULL,
                month TEXT,
                description TEXT,
                amount REAL,
                category TEXT,
                confidence TEXT,
                confidence_score REAL DEFAULT 0.7,
                needs_review INTEGER DEFAULT 0,
                reason TEXT,
                suggested_categories TEXT,
                classifier TEXT,
                source_file TEXT,
                corrected INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS txn_user_date_idx ON transactions(user_id, txn_date);
            CREATE INDEX IF NOT EXISTS txn_user_month_idx ON transactions(user_id, month);
            CREATE INDEX IF NOT EXISTS txn_user_cat_idx ON transactions(user_id, category);

            CREATE TABLE IF NOT EXISTS uploads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                filename TEXT,
                stored_at TEXT,
                rows_parsed INTEGER,
                rows_inserted INTEGER,
                low_confidence_count INTEGER,
                review_count INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS budget_caps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                month TEXT NOT NULL,
                category TEXT NOT NULL,
                cap_amount REAL NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, month, category)
            );
        """)
        self._migrate_schema()
        self.conn.commit()

    def _migrate_schema(self):
        # ĐÃ SỬA: migration nhẹ để DB cũ vẫn chạy khi bạn đã từng test local.
        cur = self.conn.execute("PRAGMA table_info(transactions)")
        existing = {row[1] for row in cur.fetchall()}
        columns = {
            "transaction_id": "TEXT",
            "month": "TEXT",
            "confidence_score": "REAL DEFAULT 0.7",
            "needs_review": "INTEGER DEFAULT 0",
            "reason": "TEXT",
            "suggested_categories": "TEXT",
            "classifier": "TEXT",
            "source_file": "TEXT",
            "corrected": "INTEGER DEFAULT 0",
        }
        for name, ddl in columns.items():
            if name not in existing:
                self.conn.execute(f"ALTER TABLE transactions ADD COLUMN {name} {ddl}")

    def add_transaction(self, user_id: str, txn: dict) -> None:
        txid = txn.get("transaction_id") or _txn_id()
        month = txn.get("month") or str(txn.get("date", ""))[:7]
        self.conn.execute(
            """
            INSERT INTO transactions (
                transaction_id, user_id, txn_date, month, description, amount,
                category, confidence, confidence_score, needs_review, reason,
                suggested_categories, classifier, source_file
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                txid,
                user_id,
                txn["date"],
                month,
                txn["description"],
                float(txn["amount"]),
                txn["category"],
                txn.get("confidence", ""),
                float(txn.get("confidence_score", 0.7)),
                1 if txn.get("needs_review", False) else 0,
                txn.get("reason", ""),
                _json_dumps(txn.get("suggested_categories", [])),
                txn.get("classifier", ""),
                txn.get("source_file", ""),
            ),
        )
        self.conn.commit()

    def add_upload(self, user_id: str, upload: dict) -> None:
        self.conn.execute(
            """
            INSERT INTO uploads (user_id, filename, stored_at, rows_parsed, rows_inserted, low_confidence_count, review_count)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                upload.get("filename"),
                upload.get("stored_at"),
                int(upload.get("rows_parsed", 0)),
                int(upload.get("rows_inserted", 0)),
                int(upload.get("low_confidence_count", 0)),
                int(upload.get("review_count", 0)),
            ),
        )
        self.conn.commit()

    def _row_to_txn(self, row) -> dict:
        return {
            "transaction_id": row["transaction_id"] or str(row["id"]),
            "date": row["txn_date"],
            "month": row["month"] or str(row["txn_date"])[:7],
            "description": row["description"],
            "amount": float(row["amount"] or 0),
            "category": row["category"],
            "confidence": row["confidence"],
            "confidence_score": float(row["confidence_score"] or 0.7),
            "needs_review": _to_bool(row["needs_review"]),
            "reason": row["reason"] or "",
            "suggested_categories": _json_loads(row["suggested_categories"]),
            "classifier": row["classifier"] or "",
            "source_file": row["source_file"] or "",
            "corrected": _to_bool(row["corrected"]),
        }

    def list_transactions(self, user_id: str, month: str | None = None) -> list:
        sql = "SELECT * FROM transactions WHERE user_id = ?"
        params: list = [user_id]
        if month:
            sql += " AND month = ?"
            params.append(month)
        sql += " ORDER BY txn_date DESC, id DESC"
        cur = self.conn.execute(sql, params)
        return [self._row_to_txn(r) for r in cur.fetchall()]

    def summary(self, user_id: str, month: str | None = None) -> dict:
        return _aggregate(self.list_transactions(user_id, month))

    def correct_transaction(self, user_id: str, transaction_id: str, category: str) -> dict:
        cur = self.conn.execute(
            "SELECT category FROM transactions WHERE user_id = ? AND transaction_id = ?",
            (user_id, transaction_id),
        )
        row = cur.fetchone()
        if not row:
            return {"updated": False, "reason": "Transaction not found"}
        old = row["category"]
        self.conn.execute(
            """
            UPDATE transactions
            SET category = ?, corrected = 1, needs_review = 0, confidence = 'user_corrected', confidence_score = 1.0
            WHERE user_id = ? AND transaction_id = ?
            """,
            (category, user_id, transaction_id),
        )
        self.conn.commit()
        return {"updated": True, "transaction_id": transaction_id, "old_category": old, "new_category": category}

    def set_budget_cap(self, user_id: str, month: str, category: str, cap_amount: float) -> None:
        self.conn.execute(
            """
            INSERT INTO budget_caps (user_id, month, category, cap_amount)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, month, category) DO UPDATE SET cap_amount = excluded.cap_amount
            """,
            (user_id, month, category, float(cap_amount)),
        )
        self.conn.commit()

    def list_budget_caps(self, user_id: str, month: str) -> list:
        cur = self.conn.execute(
            "SELECT month, category, cap_amount FROM budget_caps WHERE user_id = ? AND month = ? ORDER BY category",
            (user_id, month),
        )
        return [{"month": r["month"], "category": r["category"], "cap_amount": float(r["cap_amount"])} for r in cur.fetchall()]


# ĐÃ SỬA: giữ PostgresUserStore nhưng kế thừa SQLiteUserStore để local/hackathon không bị vỡ import.
# Nếu production thật cần RDS Postgres, nhóm có thể thay lại adapter SQL riêng.
class PostgresUserStore:
    def __init__(self, url: str):
        raise NotImplementedError("For this W7-ready version, use USERSTORE_BACKEND=sqlite locally or dynamodb on AWS.")


class DocumentDBUserStore:
    def __init__(self, url: str, db_name: str = "budgetbot", tls_ca_file: str = ""):
        raise NotImplementedError("For this W7-ready version, use USERSTORE_BACKEND=sqlite locally or dynamodb on AWS.")


class MySQLUserStore:
    def __init__(self, url: str):
        raise NotImplementedError("For this W7-ready version, use USERSTORE_BACKEND=sqlite locally or dynamodb on AWS.")


def _aggregate(rows: list) -> dict:
    agg: dict = defaultdict(lambda: {"total": 0.0, "count": 0, "review_count": 0})
    for r in rows:
        cat = r.get("category", "Other")
        agg[cat]["total"] += float(r.get("amount", 0))
        agg[cat]["count"] += 1
        if r.get("needs_review"):
            agg[cat]["review_count"] += 1
    return {k: v for k, v in agg.items()}
