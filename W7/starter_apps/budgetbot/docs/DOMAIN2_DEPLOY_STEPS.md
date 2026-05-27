# Domain 2 — BudgetBot Deploy Steps

## What was changed

ĐÃ SỬA: BudgetBot được chỉnh theo Domain 2 FinTech để demo tốt hơn:

1. AI classification có confidence_score.
2. Có needs_review cho giao dịch mơ hồ.
3. Có reason và suggested_categories.
4. Có review queue API.
5. Có user correction API.
6. Có budget cap và budget alerts API.
7. Có Lambda entrypoint bằng Mangum.
8. Có labeled_statement_eval.csv để đo accuracy.

## Local run

```powershell
cd W7\starter_apps\budgetbot
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn src.app:app --reload --port 8000
```

Open:

```text
http://localhost:8000
```

Health:

```text
http://localhost:8000/health
```

## Local demo flow

1. Upload sample_data/bank_statement_q2_2026.csv.
2. Check Spending summary.
3. Click Review queue.
4. Set cap: month 2026-05, category Food, cap 2000000.
5. Click Check alerts.
6. Open Raw API responses for evidence.

## Important local env

```env
AI_BACKEND=local
STORAGE_BACKEND=local
USERSTORE_BACKEND=sqlite
USERSTORE_SQLITE_PATH=./_data/transactions.db
SERVE_FRONTEND=true
CORS_ORIGINS=*
```

## AWS production env example

```env
AI_BACKEND=bedrock
AI_MODEL_ID=anthropic.claude-3-5-haiku-20241022-v1:0
AWS_REGION=ap-southeast-1

STORAGE_BACKEND=s3
STORAGE_BUCKET=budgetbot-statements-g9-accountid

USERSTORE_BACKEND=dynamodb
USERSTORE_TABLE=budgetbot-transactions

DEFAULT_USER_ID=test-user-001
SERVE_FRONTEND=false
CORS_ORIGINS=cloudfront-domain
```

## DynamoDB table

Table name:

```text
budgetbot-transactions
```

Keys:

```text
Partition key: user_id String
Sort key: sk String
```

## Lambda

Handler:

```text
lambda_entry.handler
```

Memory:

```text
512 MB or 1024 MB
```

Timeout:

```text
60 seconds
```

## API Gateway

Use HTTP API.

Routes:

```text
ANY /{proxy+}
ANY /
```

## Main APIs

```text
GET  /health
POST /upload
GET  /summary?month=2026-05
GET  /transactions?month=2026-05
GET  /review?month=2026-05
POST /transactions/{transaction_id}/correct
POST /budget/cap
GET  /budget/alerts?month=2026-05
```

## Evidence to capture

1. Public CloudFront URL.
2. /health response.
3. Upload result with rows_inserted.
4. Review queue showing needs_review.
5. Budget alert exceeded/ok.
6. S3 uploaded CSV.
7. DynamoDB TXN items.
8. CloudWatch logs.
9. Bedrock model access / real Bedrock output.
10. Cost Explorer screenshots.

## Evaluation

Use:

```text
sample_data/labeled_statement_eval.csv
```

Measure:

```text
accuracy = correct predictions / total rows
review_rate = needs_review rows / total rows
failure cases = named examples like FT code, GRAB, VINMART
```
