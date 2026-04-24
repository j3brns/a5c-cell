# RUNBOOK-010: SPA Edge Failure Investigation

## Symptoms
- Users reporting "403 Forbidden" or "504 Gateway Timeout" from the SPA domain.
- "BlockedRequests" alarm triggered for the SPA edge WAF.
- High rate of 4xx or 5xx errors in CloudFront metrics.

## 1. Check CloudWatch Metrics
1. Navigate to the **CloudWatch console** in `eu-west-2`.
2. View the **Platform Operations Dashboard** (or search for metrics under `AWS/CloudFront`).
3. Check the following metrics for the SPA Distribution:
   - `4xxErrorRate`
   - `5xxErrorRate`
   - `TotalErrorRate`
4. If `4xxErrorRate` is high, check the WAF metrics in `us-east-1` for `BlockedRequests`.

## 2. Inspect WAF Sampled Requests
If the `SpaEdgeBlockedRequestsAlarm` is active:
1. Switch to the **us-east-1** region in the AWS Console.
2. Navigate to **AWS WAF** -> **Web ACLs**.
3. Select the `platform-edge-security-<env>-spa-edge-waf`.
4. Go to the **Sampled requests** tab.
5. Identify which rule is blocking traffic (e.g., `AWSManagedRulesCommonRuleSet`, `AWSManagedRulesAmazonIpReputationList`).
6. Note the source IP and the nature of the request.

## 3. Query CloudFront Access Logs (Athena)
For deep investigation of edge errors or bot traffic, use Amazon Athena to query the access logs stored in `platform-spa-logs-<env>`.

### Athena Table Creation
If the table does not exist, run the following DDL in the Athena console (replace `<env>` and `<account-id>`):

```sql
CREATE EXTERNAL TABLE IF NOT EXISTS default.cloudfront_logs (
  `date` DATE,
  time STRING,
  location STRING,
  bytes INT,
  request_ip STRING,
  method STRING,
  host STRING,
  uri STRING,
  status INT,
  referrer STRING,
  user_agent STRING,
  query_string STRING,
  cookie STRING,
  result_type STRING,
  request_id STRING,
  host_header STRING,
  request_protocol STRING,
  request_bytes INT,
  time_taken FLOAT,
  xforwarded_for STRING,
  ssl_protocol STRING,
  ssl_cipher STRING,
  response_result_type STRING,
  encryption_status STRING,
  fle_status STRING,
  fle_id STRING,
  c_port INT,
  time_to_first_byte FLOAT,
  x_edge_detailed_result_type STRING,
  sc_content_type STRING,
  sc_content_len BIGINT,
  sc_range_start BIGINT,
  sc_range_end BIGINT
)
ROW FORMAT DELIMITED 
FIELDS TERMINATED BY '\t' 
LOCATION 's3://platform-spa-logs-<env>/spa-cloudfront/'
TBLPROPERTIES ( 'skip.header.line.count'='2' );
```

### Common Queries

**Top 10 IPs by request count:**
```sql
SELECT request_ip, count(*) as count
FROM cloudfront_logs
GROUP BY request_ip
ORDER BY count DESC
LIMIT 10;
```

**Find 403 Forbidden requests and their URIs:**
```sql
SELECT time, request_ip, uri, result_type
FROM cloudfront_logs
WHERE status = 403
ORDER BY time DESC;
```

**Investigate high time-taken requests:**
```sql
SELECT time, uri, time_taken
FROM cloudfront_logs
WHERE time_taken > 1.0
ORDER BY time_taken DESC;
```

## 4. Remediation
- **WAF False Positive:** If a legitimate user is being blocked by a managed rule, consider moving the rule to `COUNT` mode or adding an IP allow-list.
- **Bot Attack:** If a single IP is causing a surge in traffic, add a custom WAF rule to block the IP.
- **Backend/Origin Error:** If `5xxErrorRate` is high, investigate the S3 bucket availability or the `SpaRouteRewriteFunction` CloudFront function.
