# DA SUA: Lambda entrypoint de deploy FastAPI BudgetBot len AWS Lambda bang Mangum.
# Chay local van dung: uvicorn src.app:app --reload --port 8000
# Deploy Lambda dung handler: lambda_entry.handler
from mangum import Mangum

from src.app import app

handler = Mangum(app)
