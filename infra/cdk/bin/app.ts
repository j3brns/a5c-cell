/**
 * Platform AaaS CDK Application entry point.
 *
 * Instantiates all platform stacks in deployment order (see ARCHITECTURE.md).
 * Run: npx cdk synth --context env=dev|staging|prod
 *
 * Stack order:
 *   1. NetworkStack    — VPC, subnets, endpoints         (TASK-021)
 *   2. IdentityStack   — OIDC, pipeline roles, Entra JWKS (TASK-022)
 *   3. PlatformStorageStack — DynamoDB tables, AppConfig (ADR-703)
 *   4. PlatformSpaStack — S3, CloudFront, CSP headers     (ADR-703)
 *   5. PlatformStack   — REST API, WAF, Lambdas, Gateway  (TASK-023)
 *   6. TenantStack     — per-tenant (EventBridge-triggered) (TASK-025)
 *   7. ObservabilityStack — dashboards, alarms            (TASK-026)
 *   8. AgentCoreStack  — Runtime config eu-west-2         (TASK-024)
 */
import * as cdk from 'aws-cdk-lib';
import { AgentCoreStack } from '../lib/agentcore-stack';
import { IdentityStack } from '../lib/identity-stack';
import { NetworkStack } from '../lib/network-stack';
import { ObservabilityStack } from '../lib/observability-stack';
import { PlatformEdgeSecurityStack } from '../lib/platform-edge-security-stack';
import { PlatformStack } from '../lib/platform-stack';
import { PlatformStorageStack } from '../lib/platform-storage-stack';
import { PlatformSpaStack } from '../lib/platform-spa-stack';
import {
  AUTHORIZED_RUNTIME_REGIONS,
  EDGE_REGION,
  HOME_REGION,
  RUNTIME_NETWORK_MODE,
  SERVING_RUNTIME_REGION,
} from '../lib/runtime-topology';
import { TenantStack } from '../lib/tenant-stack';

const app = new cdk.App();

const env = app.node.tryGetContext('env') as string | undefined;
if (!env) {
  throw new Error('env context is required. Use: --context env=dev|staging|prod');
}

// These are architectural constants, not runtime configuration.

const awsEnv: cdk.Environment = {
  account: process.env['CDK_DEFAULT_ACCOUNT'],
  region: HOME_REGION,
};
const runtimeEnv: cdk.Environment = {
  account: process.env['CDK_DEFAULT_ACCOUNT'],
  region: SERVING_RUNTIME_REGION,
};
const edgeEnv: cdk.Environment = {
  account: process.env['CDK_DEFAULT_ACCOUNT'],
  region: EDGE_REGION,
};

// 1. NetworkStack
const networkStack = new NetworkStack(app, `platform-network-${env}`, {
  env: awsEnv,
  description: `Platform network infrastructure — ${env}`,
});

// 2. IdentityStack
const identityStack = new IdentityStack(app, `platform-identity-${env}`, {
  env: awsEnv,
  description: `Platform identity and pipeline roles — ${env}`,
});

// 3. PlatformStorageStack (ADR-703)
const storageStack = new PlatformStorageStack(app, `platform-storage-${env}`, {
  env: awsEnv,
  description: `Platform storage resources — ${env}`,
  envName: env,
  vpc: networkStack.vpc,
});

// 4. PlatformSpaStack (ADR-703)
const apiDomainName = app.node.tryGetContext('apiDomainName') as string | undefined;
const agUiEndpointOrigins = (app.node.tryGetContext('agUiEndpointOrigins') as string | undefined)?.split(/[,\s]+/) || [];

const spaStack = new PlatformSpaStack(app, `platform-spa-${env}`, {
  env: awsEnv,
  description: `Platform SPA resources — ${env}`,
  envName: env,
  apiDomainName,
  agUiEndpointOrigins,
});

// 5. PlatformStack
const platformStack = new PlatformStack(app, `platform-core-${env}`, {
  env: awsEnv,
  description: `Platform core services — ${env}`,
  vpc: networkStack.vpc,
  lambdaSecurityGroup: networkStack.lambdaSecurityGroup,
  storage: storageStack.storage,
  bridgeValkeyClientSecurityGroup: storageStack.bridgeValkeyClientSecurityGroup,
  spaAllowedOrigin: spaStack.spaAllowedOrigin,
  spaDistribution: spaStack.spaDistribution,
});

// CloudFront-scoped WAF resources must be created in us-east-1. This stack publishes
// the SPA web ACL ARN; pass that ARN into PlatformStack via `-c spaWebAclArn=...`
// when deploying the home-region stack so the CloudFront distribution can attach it.
new PlatformEdgeSecurityStack(app, `platform-edge-security-${env}`, {
  env: edgeEnv,
  description: `Platform CloudFront edge security (${EDGE_REGION}) — ${env}`,
  envName: env,
});

// 6. TenantStack (real deployments triggered by EventBridge per-tenant)
const tenantId = app.node.tryGetContext('tenantId');
const tenantStackId = tenantId
  ? `platform-tenant-${tenantId}-${env}`
  : `platform-tenant-stub-${env}`;

new TenantStack(app, tenantStackId, {
  env: awsEnv,
  description: tenantId
    ? `Platform resources for tenant ${tenantId} — ${env}`
    : `Platform per-tenant resources stub — ${env}`,
  authorizedRuntimeRegions: AUTHORIZED_RUNTIME_REGIONS,
});

// 7. ObservabilityStack
new ObservabilityStack(app, `platform-observability-${env}`, {
  env: awsEnv,
  description: `Platform observability — ${env}`,
  api: platformStack.api,
  apiWebAcl: platformStack.apiWebAcl,
  spaDistribution: platformStack.spaDistribution,
  bridgeFn: platformStack.bridgeFn,
  bffFn: platformStack.bffFn,
  authoriserFn: platformStack.authoriserFn,
  requestInterceptorFn: platformStack.requestInterceptorFn,
  responseInterceptorFn: platformStack.responseInterceptorFn,
  tenantsTable: platformStack.tenantsTable,
  agentsTable: platformStack.agentsTable,
  invocationsTable: platformStack.invocationsTable,
  jobsTable: platformStack.jobsTable,
  sessionsTable: platformStack.sessionsTable,
  toolsTable: platformStack.toolsTable,
  opsLocksTable: platformStack.opsLocksTable,
  billingFn: platformStack.billingFn,
  dlqs: platformStack.dlqs,
});

// 8. AgentCoreStack
new AgentCoreStack(app, `platform-agentcore-${env}`, {
  env: runtimeEnv,
  description: `Platform AgentCore configuration (${SERVING_RUNTIME_REGION}) — ${env}`,
  homeRegion: HOME_REGION,
  runtimeNetworkMode: RUNTIME_NETWORK_MODE,
  runtimeSubnetIds: networkStack.vpc.isolatedSubnets.map((subnet) => subnet.subnetId),
  runtimeSecurityGroupIds: [networkStack.agentCoreRuntimeSecurityGroup.securityGroupId],
});
