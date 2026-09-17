# Architecture and production path

## Implemented request flow

```text
Client question + bearer key
        |
        v
FastAPI validation -> server-owned key-to-tenant mapping
        |
        v
Bounded agent loop <---------------------------+
        |                                     |
        v                                     |
Demo fixture OR Bedrock Converse               |
        |                                     |
        +--> search_documents(query)          |
        |       |                             |
        |       v                             |
        |    host validates tool + arguments  |
        |       |                             |
        |    filter corpus by tenant          |
        |       |                             |
        |    rank top 3 -> tool result --------+
        |
        +--> final answer + retrieved IDs + token usage
```

Tenant identity is never accepted from the question, request body, or model. The local trusted corpus has explicit tenant metadata. A model can produce misleading text but cannot select another tenant or invoke an unimplemented tool. These controls do not eliminate hallucinations or semantic prompt injection.

## Why a small explicit loop

One read-only tool does not need a multi-agent framework. Keeping the state transition visible makes call limits, usage accounting and failure handling easy to inspect. Each request has independent in-memory state; there is no conversation history shared between callers. The injected model/corpus interfaces enable deterministic tests without credentials. A larger tool graph would justify durable orchestration, explicit state schemas and checkpointing.

## Proposed AWS deployment — not provisioned here

1. Build, scan and publish the image to ECR by immutable digest. Require tests and review before release. Create separate staging/production roles and environments.
2. Run the API in private ECS/Fargate tasks behind a TLS application edge. Set request-size, timeout, concurrency and rate limits at the edge. Prefer this simple container workload over introducing Kubernetes for one API.
3. Replace local static keys with verified OIDC/JWT identity. Derive tenant membership from validated issuer/audience/expiry and an authorized server-side membership mapping. Do not trust a client-supplied tenant header. Keep the current app private until identity integration is implemented.
4. Give the task role only the model invocation permissions/resources required for the chosen Bedrock model or inference profile. Resolve region/profile-specific resource requirements in deployment review; avoid wildcard administrator policies. Supply secrets through a managed secret store and private/controlled service egress.
5. Build a separate ingestion pipeline for approved S3 documents: validate ownership, chunk text, generate real semantic embeddings, and persist them to a chosen vector store. Include tenant and document-version metadata; enforce the tenant filter inside every retrieval query. Test deletion, revocation and cross-tenant isolation. Do not copy the in-memory hash vectors into production and call them semantic RAG.
6. Add CloudWatch/OpenTelemetry instrumentation: request/provider latency, provider failures, throttling, tool-policy failures, usage and concurrency. Avoid raw prompt/document logging by default; define redaction, retention and access controls. Current code emits basic JSON events, not these dashboards or traces.
7. Use controlled rollout with a smoke question against staging, regression evaluations, and a canary. Roll back by image digest and configuration version. Version the corpus/schema and model configuration too; image rollback alone cannot reverse a retrieval change.

No Terraform deployment, account resources or security certification are supplied by this repository. These steps are the implementation plan, not evidence that they have been completed.

## Azure adaptation — design only

| Concern | AWS route | Azure route to implement |
| --- | --- | --- |
| Container API | ECS/Fargate | Container Apps |
| Workload identity | Task IAM role | Managed identity |
| Model calls | Implemented Bedrock adapter | New Azure-hosted model adapter |
| Document storage/retrieval | S3 + selected vector store | Blob Storage + selected search/vector service |
| Secrets/telemetry | Secrets Manager / CloudWatch | Key Vault / Azure Monitor |

The orchestrator currently consumes Bedrock-shaped messages. An Azure adapter must explicitly translate tool definitions, assistant/tool results, finish states and token usage into that internal contract; changing an endpoint URL is insufficient. Add contract tests for parallel calls, refusal, content filtering, timeouts and partial/streamed output. Keep tenant authorization in application code, not provider-generated arguments. Recheck service capabilities and region availability when implementing.

## Failure model and security review

| Failure | Current behavior | Production work |
| --- | --- | --- |
| Invalid/missing key | 401, no model call | Rotate credentials; verified identity; audit denied access |
| Cross-tenant request or tool argument | Forbidden input rejected; retrieval tenant fixed by server | Storage-level policy and isolation regression suite |
| Unknown/parallel tool, duplicate ID | 422 policy error | Alert on rates; extend schema deliberately |
| Agent loops | Stop after three model calls | End-to-end deadline, cancellation, per-tenant quotas |
| Provider fails | 502 without exception text | Classify transient failures, bounded backoff, circuit breaker; avoid duplicate mutation retries |
| Context grows | Reject before next call above 40,000 serialized bytes | Model-specific token accounting and context compression |
| Empty retrieval | Demo admits insufficient context | Live-model abstention and citation evaluations |
| Expensive/failed run | Successful usage tracked; optional price estimate | Record failed-run usage safely; reconciled billing; admission budgets |

Adding a write tool changes the threat model: require narrowly scoped capabilities, validated targets, an idempotency key, a durable audit trail, and human approval for impactful actions. Do not expose a general shell or arbitrary network fetch as a shortcut.

## Before claiming production readiness

- Evaluate a versioned, tenant-separated question set with expected supporting documents, unanswerable questions, injection attempts and adversarial tenant references. Report retrieval recall, groundedness/citation correctness, abstention and leakage separately. The current suite is deterministic engineering validation, not that evaluation.
- Measure p50/p95/p99 latency, saturation, cost per successful answer and provider-throttle behavior under representative load before choosing SLO thresholds.
- Persist long-running work behind a queue if interactive request limits are exceeded. Define workflow retries/checkpoints and idempotency before adding side effects.
- Review privacy, retention/deletion, encryption and regional data residency with the actual data owners. No SOC 2, HIPAA or GDPR compliance claim follows from these code patterns.
- Complete dependency locking/scanning, container hardening, provenance/signing, restore exercises and incident runbooks with the operating team.
