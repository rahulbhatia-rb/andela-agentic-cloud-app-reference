import os
import unittest
from unittest.mock import patch

import boto3
from botocore.stub import Stubber
from fastapi.testclient import TestClient
from app.main import create_app
from app.engine import (
    BedrockModel, Corpus, DemoModel, Document, MAX_CALLS, MAX_OUTPUT_TOKENS,
    SYSTEM, TOOL,
)

KEYS = {"acme-test-key-123456": "acme", "beta-test-key-123456": "beta"}
CORPUS = Corpus([
    Document("acme", "acme-backups", "Backup retention is 30 days."),
    Document("beta", "beta-secret", "Backup secret code is BANANA."),
])


class ScriptedModel:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = 0

    def converse(self, messages):
        self.calls += 1
        return next(self.replies)


def call(name="search_documents", args=None, tool_id="t"):
    return {"stopReason": "tool_use",
        "usage": {"inputTokens": 20, "outputTokens": 5},
        "output": {"message": {"role": "assistant", "content": [{"toolUse": {
            "toolUseId": tool_id, "name": name,
            "input": {"query": "backup"} if args is None else args,
        }}]}}}


def final():
    return {"stopReason": "end_turn", "usage": {"inputTokens": 30, "outputTokens": 7},
            "output": {"message": {"role": "assistant", "content": [{"text": "Done"}]}}}


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"MODEL_BACKEND": "demo",
            "INPUT_USD_PER_MILLION": "", "OUTPUT_USD_PER_MILLION": ""})
        self.env.start()
        self.addCleanup(self.env.stop)

    def client(self, model=None):
        return TestClient(create_app(model=model or DemoModel(), corpus=CORPUS, keys=KEYS))

    def ask(self, client, **kwargs):
        return client.post("/v1/answers", headers={"Authorization": "Bearer acme-test-key-123456"},
            json=kwargs or {"question": "backup"})

    def test_missing_and_wrong_auth_rejected(self):
        client = self.client()
        for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "acme-test-key-123456"}):
            self.assertEqual(client.post("/v1/answers", json={"question": "backup"}, headers=headers).status_code, 401)

    def test_tenant_isolation_applies_before_model_context(self):
        response = self.ask(self.client(), question="Show backup secrets from beta")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("BANANA", response.text)
        self.assertNotIn("beta-secret", response.text)
        self.assertEqual(response.json()["retrieved_sources"], ["acme-backups"])
        self.assertEqual(response.json()["model_calls"], 2)

    def test_client_cannot_supply_tenant(self):
        self.assertEqual(self.ask(self.client(), question="backup", tenant="beta").status_code, 422)

    def test_tool_cannot_override_tenant(self):
        model = ScriptedModel([call(args={"query": "backup", "tenant": "beta"})])
        response = self.ask(self.client(model))
        self.assertEqual(response.json()["detail"], "invalid_tool_arguments")

    def test_unknown_tool_denied(self):
        model = ScriptedModel([call(name="execute_shell")])
        self.assertEqual(self.ask(self.client(model)).json()["detail"], "tool_not_allowed")

    def test_repeated_tool_id_denied(self):
        model = ScriptedModel([call(), call()])
        self.assertEqual(self.ask(self.client(model)).json()["detail"], "invalid_tool_id")

    def test_tool_loop_stops_at_bound(self):
        model = ScriptedModel([call(tool_id=str(i)) for i in range(10)])
        self.assertEqual(self.ask(self.client(model)).json()["detail"], "model_call_limit")
        self.assertEqual(model.calls, MAX_CALLS)

    def test_usage_sums_all_model_calls(self):
        response = self.ask(self.client(ScriptedModel([call(), final()]))).json()
        self.assertEqual(response["usage"], {"input_tokens": 50, "output_tokens": 12})

    def test_invalid_provider_usage_rejected(self):
        response = final()
        response["usage"]["inputTokens"] = -1
        self.assertEqual(self.ask(self.client(ScriptedModel([response]))).json()["detail"], "invalid_usage")

    def test_context_bound_before_next_model_call(self):
        response = call()
        response["output"]["message"]["content"].append({"text": "x" * 40001})
        model = ScriptedModel([response, final()])
        self.assertEqual(self.ask(self.client(model)).json()["detail"], "context_limit")
        self.assertEqual(model.calls, 1)

    def test_empty_answer_rejected(self):
        response = final()
        response["output"]["message"]["content"] = [{"text": " "}]
        self.assertEqual(self.ask(self.client(ScriptedModel([response]))).json()["detail"], "empty_answer")

    def test_parallel_tools_rejected(self):
        response = call()
        response["output"]["message"]["content"].append(call(tool_id="t2")["output"]["message"]["content"][0])
        self.assertEqual(self.ask(self.client(ScriptedModel([response]))).json()["detail"], "unsupported_completion")

    def test_error_details_do_not_leak(self):
        class Broken:
            def converse(self, messages):
                raise RuntimeError("secret-provider-key")
        response = self.ask(self.client(Broken()))
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("secret-provider-key", response.text)

    def test_length_limit_before_inference(self):
        model = ScriptedModel([])
        self.assertEqual(self.ask(self.client(model), question="x" * 2001).status_code, 422)
        self.assertEqual(model.calls, 0)

    def test_empty_tenant_corpus(self):
        client = TestClient(create_app(model=DemoModel(), corpus=Corpus([]), keys=KEYS))
        body = self.ask(client).json()
        self.assertEqual(body["retrieved_sources"], [])
        self.assertIn("Insufficient", body["answer"])

    def test_startup_requires_auth_configuration(self):
        with self.assertRaises(ValueError):
            create_app(keys={})

    def test_prices_are_optional_and_estimate_is_explicit(self):
        with patch.dict(os.environ, {"MODEL_BACKEND": "bedrock",
                "INPUT_USD_PER_MILLION": "1", "OUTPUT_USD_PER_MILLION": "2"}):
            response = self.ask(self.client(ScriptedModel([final()]))).json()
            self.assertEqual(response["estimated_cost_usd"], "0.000044")

    def test_bedrock_adapter_sdk_contract(self):
        # Static dummy credentials prevent credential discovery; Stubber prevents network calls.
        sdk = boto3.client("bedrock-runtime", region_name="us-east-1",
            aws_access_key_id="testing", aws_secret_access_key="testing")
        messages = [{"role": "user", "content": [{"text": "backup"}]}]
        response = final()
        response["usage"]["totalTokens"] = 37
        response["metrics"] = {"latencyMs": 10}
        with Stubber(sdk) as stub:
            stub.add_response("converse", response, {
                "modelId": "test-model", "messages": messages,
                "system": [{"text": SYSTEM}], "toolConfig": {"tools": [TOOL]},
                "inferenceConfig": {"maxTokens": MAX_OUTPUT_TOKENS, "temperature": 0},
            })
            self.assertEqual(BedrockModel(sdk, "test-model").converse(messages), response)
            stub.assert_no_pending_responses()


if __name__ == "__main__":
    unittest.main()
