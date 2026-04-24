# ADR-021: SPA CloudFront Logging Model

## Status
Proposed

## Context
The SPA frontend uses Amazon CloudFront for content delivery. We need a clear logging model to support security auditing, operational troubleshooting, and compliance requirements.

CloudFront offers two primary logging mechanisms:
1. **Standard Logs (Access Logs):** Delivered to an S3 bucket with a delay of up to an hour. Cost-effective and suitable for long-term audit.
2. **Real-time Logs:** Delivered to Kinesis Data Streams within seconds. Higher cost and requires additional infrastructure (Kinesis, Lambda/Firehose) for storage or analysis.

We also considered "Standard Logging v2" (partitioned logs), but currently prefer the established standard logging path for simplicity and consistency with other platform audit logs.

## Decision
We will use **CloudFront Standard Logs** delivered to an S3 bucket.

1. **Storage:** Logs will be stored in a dedicated S3 bucket `platform-spa-logs-${env}`.
2. **Retention:**
   - **Non-production (dev/staging):** 30 days. Sufficient for immediate troubleshooting.
   - **Production:** 365 days. Meets typical compliance and audit requirements for a B2B platform.
3. **Encryption:** S3-managed encryption (SSE-S3) is enabled on the log bucket.
4. **Access Control:** The log bucket is non-public, enforces SSL, and uses `BucketOwnerPreferred` ownership to ensure the platform account retains control over logs delivered by CloudFront.
5. **Analysis Path:** Operators will use **Amazon Athena** to query logs in S3 for ad-hoc investigation of edge errors, bot traffic, and WAF activity.

## Consequences
- **Positive:** Low cost, simple infrastructure, meets audit requirements.
- **Negative:** Up to 1-hour delay in log delivery. Real-time security response must rely on CloudWatch metrics and WAF sampled requests rather than access logs.
- **Neutral:** Requires manual setup of Athena tables for efficient querying.
