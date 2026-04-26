import * as cdk from 'aws-cdk-lib';
import * as appconfig from 'aws-cdk-lib/aws-appconfig';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as elasticache from 'aws-cdk-lib/aws-elasticache';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import { Construct } from 'constructs';

export interface PlatformStorageResources {
  readonly tenantsTable: dynamodb.Table;
  readonly agentsTable: dynamodb.Table;
  readonly toolsTable: dynamodb.Table;
  readonly opsLocksTable: dynamodb.Table;
  readonly gatewayIdempotencyTable: dynamodb.Table;
  readonly invocationsTable: dynamodb.Table;
  readonly jobsTable: dynamodb.Table;
  readonly sessionsTable: dynamodb.Table;
  readonly appconfigApp: appconfig.CfnApplication;
  readonly appconfigEnv: appconfig.CfnEnvironment;
  readonly capabilityProfile: appconfig.CfnConfigurationProfile;
  readonly capabilityDeploymentStrategy: appconfig.CfnDeploymentStrategy;
  readonly valkeyCluster: elasticache.CfnServerlessCache;
  readonly valkeyCacheName: string;
  readonly valkeySecurityGroup: ec2.SecurityGroup;
}

export function createPlatformStorage(
  scope: Construct,
  props: {
    envName: string;
    vpc: ec2.IVpc;
    valkeyClientSecurityGroup: ec2.ISecurityGroup;
  },
): PlatformStorageResources {
  const { envName, vpc, valkeyClientSecurityGroup } = props;
  const pointInTimeRecoverySpecification = {
    pointInTimeRecoveryEnabled: true,
  } satisfies dynamodb.PointInTimeRecoverySpecification;

  const tenantsTable = new dynamodb.Table(scope, 'TenantsTable', {
    tableName: 'platform-tenants',
    partitionKey: { name: 'PK', type: dynamodb.AttributeType.STRING },
    sortKey: { name: 'SK', type: dynamodb.AttributeType.STRING },
    billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
    encryption: dynamodb.TableEncryption.AWS_MANAGED,
    pointInTimeRecoverySpecification,
    deletionProtection: true,
    removalPolicy: cdk.RemovalPolicy.RETAIN,
  });

  tenantsTable.addGlobalSecondaryIndex({
    indexName: 'gsi-execution-role-arn',
    partitionKey: { name: 'executionRoleArn', type: dynamodb.AttributeType.STRING },
    projectionType: dynamodb.ProjectionType.KEYS_ONLY,
  });

  const agentsTable = new dynamodb.Table(scope, 'AgentsTable', {
    tableName: 'platform-agents',
    partitionKey: { name: 'PK', type: dynamodb.AttributeType.STRING },
    sortKey: { name: 'SK', type: dynamodb.AttributeType.STRING },
    billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
    encryption: dynamodb.TableEncryption.AWS_MANAGED,
    pointInTimeRecoverySpecification,
    deletionProtection: true,
    removalPolicy: cdk.RemovalPolicy.RETAIN,
  });

  const toolsTable = new dynamodb.Table(scope, 'ToolsTable', {
    tableName: 'platform-tools',
    partitionKey: { name: 'PK', type: dynamodb.AttributeType.STRING },
    sortKey: { name: 'SK', type: dynamodb.AttributeType.STRING },
    billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
    encryption: dynamodb.TableEncryption.AWS_MANAGED,
    pointInTimeRecoverySpecification,
    deletionProtection: true,
    removalPolicy: cdk.RemovalPolicy.RETAIN,
  });

  const opsLocksTable = new dynamodb.Table(scope, 'OpsLocksTable', {
    tableName: 'platform-ops-locks',
    partitionKey: { name: 'PK', type: dynamodb.AttributeType.STRING },
    sortKey: { name: 'SK', type: dynamodb.AttributeType.STRING },
    billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
    encryption: dynamodb.TableEncryption.AWS_MANAGED,
    timeToLiveAttribute: 'ttl',
    pointInTimeRecoverySpecification,
    deletionProtection: true,
    removalPolicy: cdk.RemovalPolicy.RETAIN,
  });

  const gatewayIdempotencyTable = new dynamodb.Table(scope, 'GatewayIdempotencyTable', {
    tableName: 'platform-gateway-idempotency',
    partitionKey: { name: 'id', type: dynamodb.AttributeType.STRING },
    billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
    encryption: dynamodb.TableEncryption.AWS_MANAGED,
    timeToLiveAttribute: 'expiration',
    pointInTimeRecoverySpecification,
    deletionProtection: true,
    removalPolicy: cdk.RemovalPolicy.RETAIN,
  });

  const invocationsTable = new dynamodb.Table(scope, 'InvocationsTable', {
    tableName: 'platform-invocations',
    partitionKey: { name: 'PK', type: dynamodb.AttributeType.STRING },
    sortKey: { name: 'SK', type: dynamodb.AttributeType.STRING },
    billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
    encryption: dynamodb.TableEncryption.AWS_MANAGED,
    timeToLiveAttribute: 'ttl',
    pointInTimeRecoverySpecification,
    deletionProtection: true,
    removalPolicy: cdk.RemovalPolicy.RETAIN,
  });

  const jobsTable = new dynamodb.Table(scope, 'JobsTable', {
    tableName: 'platform-jobs',
    partitionKey: { name: 'PK', type: dynamodb.AttributeType.STRING },
    sortKey: { name: 'SK', type: dynamodb.AttributeType.STRING },
    billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
    encryption: dynamodb.TableEncryption.AWS_MANAGED,
    timeToLiveAttribute: 'ttl',
    stream: dynamodb.StreamViewType.NEW_AND_OLD_IMAGES,
    pointInTimeRecoverySpecification,
    deletionProtection: true,
    removalPolicy: cdk.RemovalPolicy.RETAIN,
  });

  const sessionsTable = new dynamodb.Table(scope, 'SessionsTable', {
    tableName: 'platform-sessions',
    partitionKey: { name: 'PK', type: dynamodb.AttributeType.STRING },
    sortKey: { name: 'SK', type: dynamodb.AttributeType.STRING },
    billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
    encryption: dynamodb.TableEncryption.AWS_MANAGED,
    timeToLiveAttribute: 'ttl',
    pointInTimeRecoverySpecification,
    deletionProtection: true,
    removalPolicy: cdk.RemovalPolicy.RETAIN,
  });

  const appconfigApp = new appconfig.CfnApplication(scope, 'AppConfigApplication', {
    name: `platform-config-${envName}`,
  });

  const appconfigEnv = new appconfig.CfnEnvironment(scope, 'AppConfigEnvironment', {
    applicationId: appconfigApp.ref,
    name: envName,
  });

  const capabilityProfile = new appconfig.CfnConfigurationProfile(scope, 'CapabilityProfile', {
    applicationId: appconfigApp.ref,
    locationUri: 'hosted',
    name: 'tenant-capabilities',
    validators: [
      {
        type: 'JSON_SCHEMA',
        content: JSON.stringify({
          $schema: 'http://json-schema.org/draft-07/schema#',
          type: 'object',
          properties: {
            schema_version: { type: 'string' },
            capabilities: {
              type: 'object',
              additionalProperties: {
                type: 'object',
                properties: {
                  enabled: { type: 'boolean' },
                  rollout_percentage: { type: 'integer', minimum: 0, maximum: 100 },
                  tier_allow_list: {
                    type: 'array',
                    items: { enum: ['basic', 'standard', 'premium'] },
                  },
                  tenant_allow_list: { type: 'array', items: { type: 'string' } },
                },
                required: ['enabled'],
              },
            },
            killed_capabilities: { type: 'array', items: { type: 'string' } },
          },
          required: ['schema_version'],
        }),
      },
    ],
  });

  const capabilityDeploymentStrategy = new appconfig.CfnDeploymentStrategy(
    scope,
    'CapabilityDeploymentStrategy',
    {
      name: `tenant-capabilities-linear-${envName}`,
      deploymentDurationInMinutes: envName === 'prod' ? 30 : 10,
      growthFactor: envName === 'prod' ? 25 : 50,
      growthType: 'LINEAR',
      finalBakeTimeInMinutes: envName === 'prod' ? 15 : 5,
      replicateTo: 'NONE',
      description: 'Bounded rollout for tenant capability policy changes',
    },
  );

  new ssm.StringParameter(scope, 'AppConfigAppIdParam', {
    parameterName: `/platform/${envName}/config/appconfig-app-id`,
    stringValue: appconfigApp.ref,
  });

  new ssm.StringParameter(scope, 'AppConfigEnvIdParam', {
    parameterName: `/platform/${envName}/config/appconfig-env-id`,
    stringValue: appconfigEnv.ref,
  });

  new ssm.StringParameter(scope, 'AppConfigCapabilityProfileIdParam', {
    parameterName: `/platform/${envName}/config/appconfig-capability-profile-id`,
    stringValue: capabilityProfile.ref,
  });

  const defaultCapabilityConfiguration = new appconfig.CfnHostedConfigurationVersion(
    scope,
    'DefaultCapabilityConfiguration',
    {
      applicationId: appconfigApp.ref,
      configurationProfileId: capabilityProfile.ref,
      contentType: 'application/json',
      content: JSON.stringify({
        schema_version: '2026-03-21',
        capabilities: {
          'agents.invoke': {
            enabled: true,
            rollout_percentage: 100,
            tier_allow_list: ['basic', 'standard', 'premium'],
          },
          'tools.browser': {
            enabled: true,
            rollout_percentage: 100,
            tier_allow_list: ['standard', 'premium'],
          },
          'tools.get_platform_health': {
            enabled: true,
            rollout_percentage: 100,
            tier_allow_list: ['premium'],
            tenant_allow_list: ['platform'],
          },
          'tools.get_tenant_status': {
            enabled: true,
            rollout_percentage: 100,
            tier_allow_list: ['premium'],
            tenant_allow_list: ['platform'],
          },
          'tools.get_recent_errors': {
            enabled: true,
            rollout_percentage: 100,
            tier_allow_list: ['premium'],
            tenant_allow_list: ['platform'],
          },
          'tools.get_runbook_guidance': {
            enabled: true,
            rollout_percentage: 100,
            tier_allow_list: ['premium'],
            tenant_allow_list: ['platform'],
          },
        },
        killed_capabilities: [],
      }),
    },
  );

  const defaultCapabilityDeployment = new appconfig.CfnDeployment(scope, 'DefaultCapabilityDeployment', {
    applicationId: appconfigApp.ref,
    environmentId: appconfigEnv.ref,
    configurationProfileId: capabilityProfile.ref,
    configurationVersion: defaultCapabilityConfiguration.ref,
    deploymentStrategyId: capabilityDeploymentStrategy.ref,
  });
  defaultCapabilityDeployment.addDependency(defaultCapabilityConfiguration);
  defaultCapabilityDeployment.addDependency(capabilityDeploymentStrategy);

  // --- Valkey (ElastiCache Serverless) for TPM rate limiting (TASK-902) ---

  const valkeySecurityGroup = new ec2.SecurityGroup(scope, 'ValkeySecurityGroup', {
    vpc,
    allowAllOutbound: false,
    description: 'Security group for platform Valkey cluster (ElastiCache Serverless)',
  });

  valkeySecurityGroup.addIngressRule(
    valkeyClientSecurityGroup,
    ec2.Port.tcp(6379),
    'Allow Redis/Valkey access from Bridge Lambda',
  );

  const valkeyCacheName = `platform-valkey-${envName}`;
  const valkeyCluster = new elasticache.CfnServerlessCache(scope, 'ValkeyCluster', {
    engine: 'valkey',
    serverlessCacheName: valkeyCacheName,
    subnetIds: vpc.isolatedSubnets.map((subnet) => subnet.subnetId),
    securityGroupIds: [valkeySecurityGroup.securityGroupId],
  });

  new ssm.StringParameter(scope, 'ValkeyEndpointParam', {
    parameterName: `/platform/${envName}/config/valkey-endpoint`,
    stringValue: valkeyCluster.attrEndpointAddress,
    description: 'Valkey cluster endpoint for TPM rate limiting',
  });

  return {
    tenantsTable,
    agentsTable,
    toolsTable,
    opsLocksTable,
    gatewayIdempotencyTable,
    invocationsTable,
    jobsTable,
    sessionsTable,
    appconfigApp,
    appconfigEnv,
    capabilityProfile,
    capabilityDeploymentStrategy,
    valkeyCluster,
    valkeyCacheName,
    valkeySecurityGroup,
  };
}
