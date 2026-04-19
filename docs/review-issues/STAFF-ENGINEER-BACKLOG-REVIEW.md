# Staff Engineer Backlog Review

Date: 2026-04-18

Scope:
- Independent backlog review beyond `FITNESS-FOR-PURPOSE-REPORT-V2.md`
- CloudFront, SPA, certificate, WAF, and issue-process surfaces
- Existing CR00x review items are retained as valid historical inputs

Primary references inspected:
- `infra/cdk/lib/platform-spa.ts`
- `infra/cdk/lib/platform-waf.ts`
- `infra/cdk/lib/platform-api.ts`
- `spa/src/auth/AuthProvider.tsx`
- `spa/src/auth/msalConfig.ts`
- `spa/src/api/client.ts`
- `spa/src/hooks/useAgUiSession.ts`
- `docs/review-issues/DRAFT-GITLAB-ISSUES.md`

AWS documentation cross-checks used:
- CloudFront viewer certificates must use ACM certificates in `us-east-1` for viewer HTTPS.
- CloudFront response headers policies are the right native place for CSP, HSTS,
  X-Content-Type-Options, X-Frame-Options, and Referrer-Policy.
- WAFv2 WebACL `Scope=CLOUDFRONT` must be created in `us-east-1`.

## Executive Summary

The platform has strong foundations: S3 OAC, private SPA bucket, CloudFront security
headers, REST API WAF, API CORS pinning to the SPA origin, MSAL session storage, and
useful SPA tests. The backlog should not discard the CR00x set; CR007, CR008, CR009,
and CR013 remain relevant.

The largest independent gaps are:
- SPA CloudFront distribution is explicitly not WAF-protected.
- CloudFront custom-domain certificate region/domain validity is not enforced at synth time.
- SPA CSP allows `connect-src https:` to any HTTPS endpoint.
- HSTS preload/includeSubDomains is emitted even for the default `*.cloudfront.net` domain.
- SPA auth/token fallback has confusing control flow and noisy console logging.
- AG-UI SSE handling duplicates the API client's SSE parser and uses raw `fetch`.
- `AdminPage.tsx`, `InvokePage.tsx`, `api/client.ts`, and `api/contracts.ts` are bloated.
- Legacy workflow/config residue still exists or is documented as retirement work.

## Findings And Backlog Requirements

### EDGE-01: SPA CloudFront has no WAF

Severity: HIGH

Evidence:
- `platform-spa.ts` creates the SPA distribution without `webAclId`.
- `platform-stack.test.ts` explicitly asserts the SPA distribution has no `WebACLId`.
- `platform-ingress-constructs.test.ts` also asserts no CloudFront `WebACLId`.
- `platform-waf.ts` creates only a regional API Gateway WAF (`scope: 'REGIONAL'`).

Risk:
The public SPA edge can receive bot, scraper, volumetric, and exploit-probing traffic
without WAF-managed rules. The API has WAF coverage, but the public CloudFront surface
does not.

Requirement:
Add a separate CloudFront-scoped WebACL for the SPA distribution, provisioned in
`us-east-1`, with AWS-managed baseline rules and metrics.

Backlog:

```bash
make issue-create TITLE='TASK-111: Add CloudFront WAF for SPA distribution' SEQ=111 READY=1
```

Acceptance:
- Add `Scope=CLOUDFRONT` WebACL for SPA in `us-east-1`.
- Attach it to the SPA distribution with `WebACLId`.
- Include managed common/bot/IP-reputation rules in count mode first if false-positive
  risk is material.
- Add CloudWatch metrics and alarms for blocked/challenged requests.
- Replace tests that currently assert no `WebACLId`.

### EDGE-02: CloudFront certificate requirements are not enforced

Severity: HIGH

Evidence:
- `platform-spa.ts` accepts `spaCertificateArn` directly and passes it to CloudFront.
- Tests use a correct `us-east-1` ARN, but there is no runtime/synth validation.
- CloudFront requires viewer ACM certificates in `us-east-1`.

Risk:
A wrong-region certificate ARN can synthesize and then fail at CloudFront deployment time,
or create an operationally opaque certificate/domain mismatch failure.

Requirement:
Validate `spaCertificateArn` at synth time: must be ACM, `us-east-1`, same account or
explicitly allowed, and provided only with `spaDomainName`.

Backlog:

```bash
make issue-create TITLE='TASK-112: Validate SPA CloudFront certificate inputs' SEQ=112 READY=1
```

Acceptance:
- Reject `spaDomainName` without `spaCertificateArn`.
- Reject `spaCertificateArn` without `spaDomainName`.
- Reject non-ACM ARNs and ACM ARNs outside `us-east-1`.
- Add CDK tests for invalid combinations.
- Document DNS and SAN expectations for the custom SPA domain.

### EDGE-03: CSP connect-src is too broad

Severity: MEDIUM

Evidence:
- `platform-spa.ts` sets `connect-src 'self' https:`.
- SPA uses API base URL, Entra authority, BFF token refresh, and AG-UI/SSE connections.

Risk:
If any XSS lands, the CSP allows data exfiltration to any HTTPS endpoint. The current
policy is useful, but overly permissive for a production control-plane SPA.

Requirement:
Generate CSP from explicit allowed endpoints: SPA origin, configured API origin, Entra
authority, and any documented AG-UI endpoint origin. Avoid broad `https:` where possible.

Backlog:

```bash
make issue-create TITLE='TASK-113: Tighten SPA CloudFront CSP connect-src' SEQ=113 READY=1
```

Acceptance:
- Replace `connect-src 'self' https:` with explicit origins.
- Keep CSP under CloudFront's response-header policy value limit.
- Add CDK tests that assert the API and Entra origins are present and `https:` wildcard
  is absent.
- Verify AG-UI/SSE still connects.

### EDGE-04: HSTS preload is emitted for default CloudFront domains

Severity: MEDIUM

Evidence:
- `platform-spa.ts` always sets HSTS `includeSubdomains: true` and `preload: true`.
- Default deployments use `cloudFrontDefaultCertificate` and `*.cloudfront.net`.

Risk:
HSTS preload semantics should be reserved for domains the platform controls and intends
to submit/maintain as preload-safe. It is unnecessary and potentially misleading on the
default CloudFront domain.

Requirement:
Emit preload/includeSubDomains only for configured custom domains where the operator has
accepted the domain-wide commitment.

Backlog:

```bash
make issue-create TITLE='TASK-114: Gate SPA HSTS preload on custom domain readiness' SEQ=114
```

Acceptance:
- Default CloudFront domain keeps HTTPS redirect and strong TLS, but no preload directive.
- Custom domain can enable HSTS preload through explicit context/config.
- Add CDK tests for default and custom-domain header policy behavior.

### EDGE-05: SPA CloudFront logging is enabled, but retention and analysis path are unclear

Severity: MEDIUM

Evidence:
- `platform-spa.ts` creates `platform-spa-logs-${env}` with `RETAIN`.
- No lifecycle rule or documented query/analysis path was evident in the inspected files.

Risk:
Access logs can grow indefinitely and become cost/noise. Without an analysis path, logs
exist but do not support operations.

Requirement:
Add lifecycle retention and a documented access-log review path, or replace legacy access
logs with CloudFront standard logging v2/realtime logging if that is the chosen model.

Backlog:

```bash
make issue-create TITLE='TASK-115: Define SPA CloudFront access-log retention and analysis' SEQ=115
```

Acceptance:
- Add lifecycle policy for SPA log bucket.
- Document how operators inspect edge errors/bot traffic.
- Add tests for log bucket retention/lifecycle.

### EDGE-06: API WAF is minimal and UK-only rate limiting may miss abusive non-GB traffic

Severity: MEDIUM

Evidence:
- `platform-waf.ts` has common managed rules, GB-scoped rate limiting, and a sqlmap user-agent block.
- Non-GB requests only hit managed/common rules.

Risk:
Attackers outside GB can still drive high request volume up to API Gateway throttles.
The GB-only scope may match a deployment assumption, but the code does not document why.

Requirement:
Make the WAF policy explicit: either global rate limit plus GB-specific stricter rule, or
document why only GB is rate-limited.

Backlog:

```bash
make issue-create TITLE='TASK-116: Revisit API WAF rate-limit scope and managed rule baseline' SEQ=116
```

Acceptance:
- Add global rate-based rule or document the GB-only policy.
- Consider AWSManagedRulesKnownBadInputsRuleSet and AmazonIpReputationList.
- Add tests for rule ordering and metric names.

### SPA-01: SPA auth fallback flow is too complex and logs auth errors to console

Severity: MEDIUM

Evidence:
- `AuthProvider.tsx` has nested MSAL silent, popup, BFF OBO fallback, and fallback-to-popup logic.
- It logs auth failures and BFF fallback failures with raw error objects.

Risk:
The auth path is hard to reason about and can expose operational or token-acquisition
details in browser consoles. It also mixes user-interactive fallback with BFF fallback.

Requirement:
Simplify token acquisition into explicit paths and sanitize console output.

Backlog:

```bash
make issue-create TITLE='TASK-117: Simplify SPA token refresh and sanitize auth logging' SEQ=117
```

Acceptance:
- Separate silent MSAL, BFF OBO, and interactive popup paths.
- Do not log raw error objects in production builds.
- Add tests for each fallback path and no-recursion behavior.

### SPA-02: AG-UI SSE bypasses the shared API client parser/executor

Severity: MEDIUM

Evidence:
- `api/client.ts` already has `SseClient` and authenticated request execution.
- `useAgUiSession.ts` performs a raw `fetch`, parses SSE independently, and handles auth manually.

Risk:
Two SSE parsers and two auth request paths will drift. Retry, 401 refresh, and parser
fixes may land in one path but not the other.

Requirement:
Make AG-UI streaming use the shared API client stream path or extract one shared SSE
parser used by both paths.

Backlog:

```bash
make issue-create TITLE='TASK-118: Reuse shared SPA SSE client for AG-UI sessions' SEQ=118
```

Acceptance:
- One SSE parser implementation is used by API streaming and AG-UI streaming.
- AG-UI stream gets the same auth refresh behavior as other authenticated requests, unless
  intentionally different and documented.
- Existing AG-UI tests remain green.

### SPA-03: Admin and invoke pages are too large for safe iteration

Severity: LOW / MEDIUM

Evidence:
- `AdminPage.tsx` is 539 lines.
- `InvokePage.tsx` is 390 lines.
- `api/client.ts` is 438 lines; `api/contracts.ts` is 502 lines.

Risk:
These are not automatically bugs, but they are large enough that future UI changes will
be harder to review and test. The risk is highest in `AdminPage`, where operational data,
health, quota, tenant, and audit flows are likely mixed.

Requirement:
Split by UI responsibility only after behavior is protected by tests. Do not add a design
system or state library just to reduce line count.

Backlog:

```bash
make issue-create TITLE='TASK-119: Split SPA AdminPage into focused panels' SEQ=119
make issue-create TITLE='TASK-120: Split SPA InvokePage flow components' SEQ=120
```

Acceptance:
- Extract presentational panels/hooks with no behavior change.
- Keep existing tests green and add regression tests for extracted boundaries.
- No new dependencies.

### OPS-01: Deprecated and parallel workflow surfaces are over-present

Severity: MEDIUM

Evidence:
- `scripts/task.py` and `make task-*` remain in the tree.
- `scripts/worktree_issues.py` is a compatibility shim.
- `scripts/codex_flow/` overlaps with issue_tool.
- Historical CR files and draft backlog files can become parallel trackers if not retired.

Risk:
Agents and humans can choose different workflows and create state drift. This is already
the failure mode that triggered the GitLab issue-process migration.

Requirement:
Retire before refactor. Do not extract or polish legacy surfaces until deciding what
survives.

Backlog:
Already covered in `DRAFT-GITLAB-ISSUES.md` as `TASK-401` through `TASK-404` and
`TASK-501`.

## Keep Existing CR00x Items

Do not discard the CR review set. Treat it as historical evidence and create/keep live
issues for unresolved or partially resolved items:
- CR007: bootstrap dual-key attributes
- CR008: reserved tenant ID semantics
- CR009: explicit control-plane/admin DynamoDB access boundary
- CR013: bridge handler facade bloat

The fixed CR001-CR006 and CR010-CR012 can remain as historical review records until the
GitLab migration is complete; they do not need new live issues unless regression evidence
appears.

## Missing From Current Draft Backlog

Add these to `DRAFT-GITLAB-ISSUES.md` before creating live issues:
- `TASK-111`: Add CloudFront WAF for SPA distribution
- `TASK-112`: Validate SPA CloudFront certificate inputs
- `TASK-113`: Tighten SPA CloudFront CSP `connect-src`
- `TASK-114`: Gate SPA HSTS preload on custom domain readiness
- `TASK-115`: Define SPA CloudFront access-log retention and analysis
- `TASK-116`: Revisit API WAF rate-limit scope and managed rule baseline
- `TASK-117`: Simplify SPA token refresh and sanitize auth logging
- `TASK-118`: Reuse shared SPA SSE client for AG-UI sessions
- `TASK-119`: Split SPA AdminPage into focused panels
- `TASK-120`: Split SPA InvokePage flow components

## What Is Over-Present / Bloated

- Workflow surfaces: `issue_tool`, `task.py`, `worktree_issues.py`, and `codex_flow` are
  too many ways to manage work.
- Review documents: CR files plus fitness report plus draft backlog are useful now, but
  should be collapsed after GitLab issues are created.
- SPA page components: `AdminPage.tsx` and `InvokePage.tsx` are too large.
- API client/contracts: `api/client.ts` and `api/contracts.ts` may be justified by generated
  contract checks, but should not keep accumulating hand-written behavior.
- CloudFront tests currently preserve the absence of SPA WAF; that is documentation of a
  gap, not a safety property.

## What Is Missing

- CloudFront-scoped WAF for SPA.
- Synth-time validation of CloudFront certificate region/input pairs.
- Explicit CSP allow-list tied to configured API/Entra origins.
- HSTS preload policy based on custom-domain readiness.
- SPA edge log lifecycle and operational analysis path.
- Clear WAF policy for non-GB traffic.
- Shared SSE implementation for API stream and AG-UI stream.
- Sanitized SPA auth logging.
- A retirement plan for the draft backlog after GitLab issues are created.
