# Tenant Knowledge Agent — Andela application reference

A small Python/FastAPI application demonstrating a bounded, tool-calling knowledge assistant: authenticate a tenant, retrieve only its documents, and return an answer with retrieval provenance and provider-reported token usage.

Prepared by **Rahul H Bhatia** for **Senior Cloud Software Engineer — AI / Agentic Applications**. This is an independent portfolio project, not an Andela product or a claim of production deployment.

## Why this project

The role calls for building cloud applications, not only operating their infrastructure. The core deliverable here is an executable API with application logic, a Bedrock adapter, and failure-path tests. Infrastructure is discussed as a deployment boundary, not substituted for the application.

My background includes AWS/GCP cloud and platform engineering, reusable infrastructure, delivery automation, and production operations. I have also worked on setting up generative AI applications on AWS, their RAG structure, and deploying those applications using the AWS stack. This repository is a new, inspectable demonstration of my approach; it does not imply a particular duration of Python/GenAI experience or production Azure experience.

## Reviewer guide

1. Run the no-cloud quickstart below; inspect the interactive API at `/docs`.
2. Read [`app/engine.py`](app/engine.py): tenant-scoped retrieval, the model/tool exchange, and enforced limits.
3. Read [`tests/test_api.py`](tests/test_api.py): authorization, isolation, invalid tool calls, bounded execution, usage, and provider failure handling.
4. Read [`docs/architecture.md`](docs/architecture.md): security boundaries, AWS delivery plan, Azure adaptation, and what must change before production.

| Role requirement | Evidence in this repository | Scope |
| --- | --- | --- |
| Python APIs and application engineering | FastAPI factory, validated request schema, explicit HTTP failures | Implemented |
| Agents and tool/function calling | Bedrock Converse adapter and a maximum-three-call orchestration loop | SDK contract tested; live inference not tested |
| RAG and vector retrieval | Tenant-filtered, normalized hashed lexical vectors and top-three retrieval | Runnable fixture; not learned semantic embeddings or a managed vector database |
| Security and reliability | Server-derived tenant, read-only tool allowlist, context bound, sanitized errors | Reference controls, not a security certification |
| Observability and cost awareness | Request IDs, structured log events, trajectory and aggregated token usage | No tracing backend; cost estimate requires configured prices |
| Delivery | Non-root Docker image definition and GitHub Actions tests/build | No cloud infrastructure deployed |
| AWS and Azure | AWS adapter; documented Azure migration boundaries | Azure implementation is future work |

## Run locally — no AWS account or model charges

Use Python 3.12 or 3.13. Run all commands from the repository root.

```bash
git clone https://github.com/rahulbhatia-rb/andela-agentic-cloud-app-reference.git
cd andela-agentic-cloud-app-reference
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export MODEL_BACKEND=demo
export API_KEYS_JSON='{"local-demo-key-123456789":"acme"}'
uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

In another terminal:

```bash
curl --fail-with-body http://127.0.0.1:8000/v1/answers \
  -H 'Authorization: Bearer local-demo-key-123456789' \
  -H 'Content-Type: application/json' \
  -d '{"question":"What is our backup retention policy?"}'
```

The demo returns matching **acme** document excerpts, their IDs, `backend: "demo"`, two model-fixture calls, zero tokens, and a null cost estimate. Exact retrieval ordering depends on lexical overlap. `DemoModel` is a deterministic fixture: **it does not call an LLM or perform AI inference**. The public key above is only for localhost testing; replace it before any shared deployment.

The request has no tenant field. Supplying one is rejected. The API maps the bearer key to a tenant, and retrieval filters that tenant before ranking and before content reaches the model.

## Run the tests

```bash
python -m unittest discover -s tests -v
```

The 18 deterministic tests make no AWS/model calls. They cover missing/incorrect authentication, tenant isolation, forbidden tenant overrides, unknown and parallel tools, duplicate tool IDs, call/context/input limits, empty answers, empty retrieval, token accounting, invalid usage, startup configuration, error sanitization, cost arithmetic, and the Bedrock SDK request contract. These are application tests, not an evaluation of live model quality or resistance to every prompt-injection attack.

GitHub Actions runs tests on Python 3.12/3.13 and builds the container. Direct dependencies are version-pinned in `requirements.txt`; this is not a complete transitive dependency lock.

## Optional: use an actual Bedrock model

This path can incur AWS charges. Select a model available in your account/region that supports Converse tool use. Configure AWS credentials through your normal credential chain; never put credentials in code or git. On AWS, use a workload role.

```bash
export MODEL_BACKEND=bedrock
export AWS_REGION=us-east-1
export BEDROCK_MODEL_ID='<your-tool-capable-model-or-inference-profile-id>'
# Keep API_KEYS_JSON configured, using a new private key.
uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

The model can request only `search_documents(query)`. The application validates the call, executes retrieval with the authenticated tenant, then returns a tool result for the next model turn. SDK retries are disabled; connection/read timeouts are 3/30 seconds. The orchestrator allows at most three calls and requests at most 256 output tokens per call. Unsupported completion states fail closed.

Optionally configure **both** `INPUT_USD_PER_MILLION` and `OUTPUT_USD_PER_MILLION` with the prices applicable to your selected model and usage tier. Otherwise `estimated_cost_usd` is null. Prices are not fetched automatically. This estimate excludes other AWS charges, cached/reasoning-token pricing differences, and failed-run accounting; it is not a billing ledger or a hard dollar budget.

See the official [Bedrock Converse API](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html) for model support and request fields. No live Bedrock invocation is claimed by this project.

## Container

```bash
docker build -t tenant-knowledge-agent .
docker run --rm -p 127.0.0.1:8000:8000 \
  -e MODEL_BACKEND=demo -e API_KEYS_JSON \
  tenant-knowledge-agent
```

The image runs as a non-root user. The command forwards your existing `API_KEYS_JSON` variable; it does not bake that key into the image.

## Contract and limitations

- `GET /healthz`: process health/backend only, not a provider readiness probe.
- `POST /v1/answers`: question length 1–2,000 characters; unknown input fields rejected. Returns answer, retrieved source IDs, tool trajectory, model calls, usage, request ID, backend and optional estimate.
- HTTP 401: invalid credentials. HTTP 422: invalid input or an agent-policy violation. HTTP 502: sanitized provider/runtime failure.
- Retrieval uses synthetic documents in `data/documents.json`; no employer or customer data is included. Hash collisions and lexical matching can produce irrelevant results.
- `retrieved_sources` means documents retrieved, not proof that the answer is grounded or its citations correct. The model can finish without retrieving; production should enforce evidence requirements for supported question types and evaluate groundedness separately.
- There is no shell, arbitrary URL-fetch, deployment, or write tool. Prompt instructions are not a security boundary; host-enforced authorization and tool validation are.
- The 40,000-byte history limit is not a provider token limit and excludes fixed system/tool configuration. There is no global request deadline, concurrency cap, rate limiting, streaming, durable workflow state, or distributed cancellation.
- Static keys, an in-memory corpus, and synchronous execution are demonstration choices. Production needs identity integration, managed retrieval, dependency/security review, load tests, monitoring and model evaluations before launch.

## Contact

Rahul H Bhatia  
[rahulbhatia1998@gmail.com](mailto:rahulbhatia1998@gmail.com) · +91 9884541449  
[LinkedIn](https://www.linkedin.com/in/rahul-h-bhatia/) · [Portfolio](https://rahulhbhatia.vercel.app) · [Credly](https://www.credly.com/users/rahul-h-bhatia/badges)
