# RUNBOOK-001: Runtime Region Failover

Status: pre-v0.2 runtime-failover procedure.

[ADR-023](../decisions/ADR-023-v0-2-secure-deployment-contract.md) defines the v0.2
secure deployment target as a single `eu-west-2` serving runtime with staging/prod
`NetworkMode: VPC` and no `eu-west-1` fallback. Runtime regional failover is deferred
for v0.2. Do not use this runbook as approval to preserve Dublin fallback in the v0.2
implementation; replace it with a degradation procedure in the implementation issue.

## Trigger
- CloudWatch alarm: platform-runtime-region-failover fires
- Bridge Lambda logs: ServiceUnavailableException from eu-west-1
- Error rate >5% with runtime_region=eu-west-1 in bridge Lambda logs

## Severity: HIGH — active customer impact

## Immediate Actions (target: <5 minutes to failover)

### 1. Verify the outage
```bash
# Check AWS Health and the CloudWatch alarm state for eu-west-1 AgentCore.
# Confirm with bridge logs before acting:
aws health describe-events --region us-east-1 --filter services=bedrock-agentcore
make logs-bridge ENV=prod MINUTES=5 | grep ServiceUnavailableException
# If confirmed: proceed. If uncertain: wait 2 minutes, re-check.
```

### 2. Acquire distributed lock
```bash
make failover-lock-acquire ENV=prod
# IMPORTANT: must succeed before calling the failover API
# If lock already held: another operator is acting — coordinate before proceeding
# Expected output: Lock acquired: platform-runtime-failover
```

### 3. Switch runtime region
```bash
make infra-set-runtime-region REGION=eu-central-1 ENV=prod
# Calls POST /v1/platform/failover
# Uses the saved lock token from step 2 unless LOCK_ID is passed explicitly
# Bridge Lambda caches this for 60s — allow 90s for all instances to pick up
# Expected output: Runtime region updated to eu-central-1
```

### 4. Verify traffic routing
```bash
make logs-bridge ENV=prod MINUTES=5 | grep runtimeRegion
# Should show: "runtimeRegion":"eu-central-1" on new invocations
# Expected output: eu-central-1 visible within 90 seconds
```

### 5. Monitor error rate
```bash
aws cloudwatch get-metric-statistics \
  --namespace Platform/Bridge \
  --metric-name ErrorRate \
  --start-time "$(date -u -d '10 minutes ago' +%FT%TZ)" \
  --end-time "$(date -u +%FT%TZ)" \
  --period 300 \
  --statistics Average \
  --dimensions Name=Environment,Value=prod
# Error rate should drop below 1% within 2 minutes.
# If not: check AgentCore status in Frankfurt and bridge logs.
```

### 6. Release lock
```bash
make failover-lock-release ENV=prod
# Expected output: Lock released
```

## Recovery (when Dublin is restored)

### 1. Confirm Dublin recovery
```bash
aws health describe-events --region us-east-1 --filter services=bedrock-agentcore
make logs-bridge ENV=prod MINUTES=5 | grep runtimeRegion
# Confirm eu-west-1 AgentCore has recovered and new bridge invocations can route back.
```

### 2. Acquire lock, switch back, release
```bash
make failover-lock-acquire ENV=prod
make infra-set-runtime-region REGION=eu-west-1 ENV=prod
# Wait 90 seconds, verify logs
make failover-lock-release ENV=prod
```

## Post-Incident
- File incident report within 24 hours
- Check Frankfurt quota headroom after failover (higher latency = longer sessions = more quota)
- Confirm bridge/webhook DLQ messages from during failover have been processed by inspecting the
  SQS DLQs directly with AWS CLI or the read-only console.

## Notes
- Frankfurt adds ~25ms RTT vs Dublin's ~12ms — visible in P99 latency metrics
- If Frankfurt is also unavailable: platform is degraded, no further failover option
- Data remains in eu-west-2 throughout — only compute changes
