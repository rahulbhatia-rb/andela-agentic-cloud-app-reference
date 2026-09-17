from __future__ import annotations
import hmac
import json
import logging
import os
import time
import uuid
from decimal import Decimal
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from app.engine import BedrockModel, Corpus, DemoModel, Document, PolicyError, run_agent

logger = logging.getLogger("agent_api")


class Query(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=2000)


def create_app(model=None, corpus=None, keys=None):
    keys = keys if keys is not None else json.loads(os.environ.get("API_KEYS_JSON", "{}"))
    if not isinstance(keys, dict) or not keys or any(
        not isinstance(k, str) or len(k) < 16 or not isinstance(v, str) or not v
        for k, v in keys.items()
    ):
        raise ValueError("API_KEYS_JSON must map keys of at least 16 characters to tenant IDs")
    mode = os.environ.get("MODEL_BACKEND", "demo")
    if mode not in ("demo", "bedrock"):
        raise ValueError("MODEL_BACKEND must be demo or bedrock")
    if model is None:
        if mode == "bedrock":
            import boto3
            from botocore.config import Config
            client = boto3.client("bedrock-runtime", region_name=os.environ["AWS_REGION"],
                config=Config(connect_timeout=3, read_timeout=30, retries={"total_max_attempts": 1}))
            model = BedrockModel(client, os.environ["BEDROCK_MODEL_ID"])
        else:
            model = DemoModel()
    if corpus is None:
        with open(os.environ.get("CORPUS_PATH", "data/documents.json")) as f:
            corpus = Corpus([Document(**doc) for doc in json.load(f)])
    rates = [os.environ.get("INPUT_USD_PER_MILLION"), os.environ.get("OUTPUT_USD_PER_MILLION")]
    if bool(rates[0]) != bool(rates[1]):
        raise ValueError("Configure both token prices or neither")
    prices = [Decimal(r) for r in rates] if all(rates) else None
    if prices and any(not p.is_finite() or p < 0 for p in prices):
        raise ValueError("Prices must be finite and non-negative")
    api = FastAPI(title="Tenant Knowledge Agent", version="0.1.0")

    @api.get("/healthz")
    def health():
        return {"status": "ok", "backend": mode}

    @api.post("/v1/answers")
    def answer(query: Query, authorization: Optional[str] = Header(default=None)):
        token = (authorization or "").removeprefix("Bearer ")
        tenant = next((v for k, v in keys.items() if hmac.compare_digest(token.encode(), k.encode())), None)
        if tenant is None or not (authorization or "").startswith("Bearer "):
            raise HTTPException(status_code=401, detail="invalid_credentials")
        started = time.monotonic()
        request_id = str(uuid.uuid4())
        try:
            result = run_agent(tenant, query.question, model, corpus)
        except PolicyError as error:
            logger.info(json.dumps({"request_id": request_id, "outcome": str(error)}))
            raise HTTPException(status_code=422, detail=str(error)) from None
        except Exception:
            # Provider errors can contain credentials or prompts; never echo them.
            logger.warning(json.dumps({"request_id": request_id, "outcome": "provider_failure"}))
            raise HTTPException(status_code=502, detail="provider_failure") from None
        usage = result["usage"]
        estimate = None
        if prices is not None and mode == "bedrock":
            estimate = str((usage["input_tokens"] * prices[0] +
                            usage["output_tokens"] * prices[1]) / Decimal(1000000))
        result.update({"request_id": request_id, "backend": mode, "estimated_cost_usd": estimate})
        logger.info(json.dumps({"request_id": request_id, "outcome": "success",
            "latency_ms": round(1000 * (time.monotonic() - started)),
            "model_calls": result["model_calls"], **usage}))
        return result

    return api

