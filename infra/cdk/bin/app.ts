/**
 * Platform AaaS CDK Application entry point.
 *
 * Instantiates all platform stacks in deployment order (see ARCHITECTURE.md).
 * Run: npx cdk synth --context env=dev|staging|prod
 *
 * Stack order:
 *   1. NetworkStack    — VPC, subnets, endpoints         (TASK-021)
 *   2. IdentityStack   — OIDC, pipeline roles, Entra JWKS (TASK-022)
 *   3. PlatformStack   — REST API, WAF, Lambdas, Gateway  (TASK-023)
 *   4. TenantStack     — per-tenant (EventBridge-triggered) (TASK-025)
 *   5. ObservabilityStack — dashboards, alarms            (TASK-026)
 *   6. AgentCoreStack  — Runtime config eu-west-2         (TASK-024)
 */
import * as cdk from 'aws-cdk-lib';
import { AgentCoreStack } from '../lib/agentcore-stack';
import { IdentityStack } from '../lib/identity-stack';
import { NetworkStack } from '../lib/network-stack';
import { ObservabilityStack } from '../lib/observability-stack';
import { PlatformEdgeSecurityStack } from '../lib/platform-edge-security-stack';
import { PlatformStack } from '../lib/platform-stack';
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

// 3. PlatformStack
const platformStack = new PlatformStack(app, `platform-core-${env}`, {
  env: awsEnv,
  description: `Platform core services — ${env}`,
  vpc: networkStack.vpc,
  lambdaSecurityGroup: networkStack.lambdaSecurityGroup,
});

// CloudFront-scoped WAF resources must be created in us-east-1. This stack publishes
// the SPA web ACL ARN; pass that ARN into PlatformStack via `-c spaWebAclArn=...`
// when deploying the home-region stack so the CloudFront distribution can attach it.
new PlatformEdgeSecurityStack(app, `platform-edge-security-${env}`, {
  env: edgeEnv,
  description: `Platform CloudFront edge security (${EDGE_REGION}) — ${env}`,
  envName: env,
});

// 4. TenantStack (real deployments triggered by EventBridge per-tenant)
const tenantId = app.node.tryGetContext('tenantId');
const stackId = tenantId
  ? `platform-tenant-${tenantId}-${env}`
  : `platform-tenant-stub-${env}`;

new TenantStack(app, stackId, {
  env: awsEnv,
  description: tenantId
    ? `Platform resources for tenant ${tenantId} — ${env}`
    : `Platform per-tenant resources stub — ${env}`,
  authorizedRuntimeRegions: AUTHORIZED_RUNTIME_REGIONS,
});

// 5. ObservabilityStack
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

// 6. AgentCoreStack
new AgentCoreStack(app, `platform-agentcore-${env}`, {
  env: runtimeEnv,
  description: `Platform AgentCore configuration (${SERVING_RUNTIME_REGION}) — ${env}`,
  homeRegion: HOME_REGION,
  runtimeNetworkMode: RUNTIME_NETWORK_MODE,
  runtimeSubnetIds: networkStack.vpc.isolatedSubnets.map((subnet) => subnet.subnetId),
  runtimeSecurityGroupIds: [networkStack.agentCoreRuntimeSecurityGroup.securityGroupId],
});
