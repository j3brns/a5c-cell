# RUNBOOK-001: Runtime Region Degradation

Status: v0.2 runtime-degradation procedure.

[ADR-023](../decisions/ADR-023-v0-2-secure-deployment-contract.md) defines one
serving AgentCore Runtime region: `eu-west-2`. Runtime regional failover is deferred
for v0.2, and `eu-west-1` is not a rollback or fallback target.

## Trigger
- Bridge Lambda logs `ServiceUnavailableException` from `eu-west-2`
- Error rate rises on Runtime invocation paths
- AWS Health reports an AgentCore Runtime event in Europe (London)

## Severity: HIGH — active customer impact

## Immediate Actions

### 1. Verify the outage
```bash
aws health describe-events --region us-east-1 --filter services=bedrock-agentcore
make logs-bridge ENV=prod MINUTES=5 | grep ServiceUnavailableException
```

Confirm that failures are Runtime availability failures, not authoriser, tenant
execution-role, AppConfig, or tenant data-access failures.

### 2. Declare degraded runtime mode

There is no approved runtime failover command for v0.2. Do not run
`make infra-set-runtime-region` to route traffic to another region.

Record:
- affected environment
- first failed invocation timestamp
- current Bridge error rate
- AWS Health event ID, if present

### 3. Pause risky operations

Pause tenant-impacting releases and agent promotions until new invocations succeed
again in `eu-west-2`.

### 4. Monitor recovery
```bash
make logs-bridge ENV=prod MINUTES=5 | grep runtimeRegion
aws cloudwatch get-metric-statistics \
  --namespace Platform/Bridge \
  --metric-name ErrorRate \
  --start-time "$(date -u -d '10 minutes ago' +%FT%TZ)" \
  --end-time "$(date -u +%FT%TZ)" \
  --period 300 \
  --statistics Average \
  --dimensions Name=Environment,Value=prod
```

Expected recovery signal: new Bridge invocations route to `eu-west-2` and Runtime
errors return below the incident threshold.

## Post-Incident
- File incident report within 24 hours.
- Record whether runtime VPC configuration, service-linked role creation, or endpoint
  reachability contributed.
- Confirm Bridge/webhook DLQ messages from the incident window have been processed.
- Open a separate decision issue if operators need regional failover for a later
  release. That design must not reintroduce Dublin by default.
