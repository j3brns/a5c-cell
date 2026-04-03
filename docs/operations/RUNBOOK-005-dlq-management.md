# RUNBOOK-005: DLQ Management

## Trigger
- CloudWatch alarm: any DLQ depth > 0

## DLQ Inventory
| DLQ Name                            | Source Lambda         | Max receive count |
|-------------------------------------|-----------------------|-------------------|
| platform-bridge-dlq-{env}           | bridge                | 3                 |
| platform-authoriser-dlq-{env}       | authoriser            | 3                 |
| platform-tenant-api-dlq-{env}       | tenant-api            | 3                 |
| platform-interceptor-req-dlq-{env}  | request-interceptor   | 3                 |
| platform-webhook-dlq-{env}          | webhook-delivery      | 3                 |

## Immediate Actions

### 1. Inspect the DLQ
```bash
# Resolve the DLQ URL first if you only know the queue name:
# aws sqs get-queue-url --queue-name platform-bridge-dlq-prod
aws sqs receive-message \
  --queue-url "${BRIDGE_DLQ_URL}" \
  --max-number-of-messages 10 \
  --attribute-names All \
  --message-attribute-names All
# Review message body, error reason, receive count, and first received timestamp.
```

### 2. Diagnose root cause from message content
Common causes:
- Bridge DLQ: Runtime unavailable (check RUNBOOK-001), tenant role assumption failed
- Authoriser DLQ: Secrets Manager throttling (check /tmp cache is working), Entra JWKS unreachable
- Interceptor DLQ: Idempotency table issue, JWKS fetch failure
- Webhook DLQ: Tenant webhook URL unreachable (4xx or 5xx), network issue

### 3. Fix the root cause first
Do NOT redrive until root cause is fixed. Redriving into a broken system just puts
messages back in the DLQ.

### 4. Redrive after fix
```bash
# Redrive the DLQ directly with AWS CLI or the console after the root cause is fixed.
# Lambda will retry up to max receive count again.
```

### 5. Monitor after redrive
```bash
make logs-bridge ENV=prod MINUTES=10 | grep -E "ERROR|Exception"
# Confirm bridge errors are no longer repeating after redrive.
```

## Native Async Note
There is no dedicated async-runner DLQ in the native AgentCore async model.
Async lifecycle failures surface through:
- bridge DLQ (submission/dispatch path)
- webhook DLQ (delivery path)
- job status polling (`GET /v1/jobs/{jobId}`) for terminal failure metadata
