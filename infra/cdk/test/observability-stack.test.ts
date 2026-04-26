import * as cdk from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as wafv2 from 'aws-cdk-lib/aws-wafv2';
import { ObservabilityStack } from '../lib/observability-stack';
import { PlatformStack } from '../lib/platform-stack';

describe('ObservabilityStack (TASK-026)', () => {
  const synthStack = () => {
    const app = new cdk.App({
      context: {
        env: 'dev',
        entraTenantId: '00000000-0000-0000-0000-000000000000',
        entraAudience: 'platform-api',
      },
    });
    const env = { account: '123456789012', region: 'eu-west-2' };

    const networkStack = new cdk.Stack(app, 'NetworkStack', { env });
    const mockVpc = new ec2.Vpc(networkStack, 'MockVpc', {
      subnetConfiguration: [
        {
          name: 'Public',
          subnetType: ec2.SubnetType.PUBLIC,
        },
        {
          name: 'Isolated',
          subnetType: ec2.SubnetType.PRIVATE_ISOLATED,
        },
      ],
    });
    const lambdaSecurityGroup = new ec2.SecurityGroup(networkStack, 'MockLambdaSecurityGroup', {
      vpc: mockVpc,
      description: 'Trusted SG for platform Lambdas',
    });

    const platformStack = new PlatformStack(app, 'PlatformStack', {
      env,
      vpc: mockVpc,
      lambdaSecurityGroup,
    });

    const observabilityStack = new ObservabilityStack(app, 'ObservabilityStack', {
      env,
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

    return Template.fromStack(observabilityStack);
  };

  test('creates CloudWatch Dashboards (Ops + Tenant Usage)', () => {
    const template = synthStack();
    template.resourceCountIs('AWS::CloudWatch::Dashboard', 2);
    template.hasResourceProperties('AWS::CloudWatch::Dashboard', {
      DashboardName: Match.stringLikeRegexp('platform-ops-ObservabilityStack'),
    });
    template.hasResourceProperties('AWS::CloudWatch::Dashboard', {
      DashboardName: Match.stringLikeRegexp('platform-tenant-usage-ObservabilityStack'),
    });
  });

  test('Tenant Usage Dashboard includes tenant and tier variables', () => {
    const template = synthStack();
    const dashboards = template.findResources('AWS::CloudWatch::Dashboard') as Record<
      string,
      { Properties?: { DashboardBody?: string | Record<string, unknown>; DashboardName?: string } }
    >;
    const tenantDashboard = Object.values(dashboards).find((d) =>
      d.Properties?.DashboardName?.includes('platform-tenant-usage'),
    );
    expect(tenantDashboard).toBeDefined();

    const dashboardBody = JSON.stringify(tenantDashboard!.Properties!.DashboardBody);
    expect(dashboardBody).toContain('\\"variables\\"');
    expect(dashboardBody).toContain('\\"id\\":\\"tenantId\\"');
    expect(dashboardBody).toContain('\\"type\\":\\"pattern\\"');
    expect(dashboardBody).toContain('\\"pattern\\":\\"TENANT_ID_PLACEHOLDER\\"');
    expect(dashboardBody).toContain('\\"inputType\\":\\"input\\"');
    expect(dashboardBody).toContain('\\"defaultValue\\":\\"TENANT_ID\\"');
    expect(dashboardBody).toContain('\\"id\\":\\"tenantTier\\"');
    expect(dashboardBody).toContain('\\"pattern\\":\\"TENANT_TIER_PLACEHOLDER\\"');
    expect(dashboardBody).toContain('\\"inputType\\":\\"select\\"');
    expect(dashboardBody).toContain('\\"defaultValue\\":\\"basic\\"');
    expect(dashboardBody).toContain('TENANT_ID_PLACEHOLDER');
    expect(dashboardBody).toContain('TENANT_TIER_PLACEHOLDER');
    expect(dashboardBody).not.toContain('\\"search\\"');
    expect(dashboardBody).not.toContain('\\"populateFrom\\"');
  });

  test('Tenant Usage Dashboard scopes billing metrics by tenant and tier', () => {
    const template = synthStack();
    const dashboards = template.findResources('AWS::CloudWatch::Dashboard') as Record<
      string,
      { Properties?: { DashboardBody?: string | Record<string, unknown>; DashboardName?: string } }
    >;
    const tenantDashboard = Object.values(dashboards).find((d) =>
      d.Properties?.DashboardName?.includes('platform-tenant-usage'),
    );
    expect(tenantDashboard).toBeDefined();

    const dashboardBody = JSON.stringify(tenantDashboard!.Properties!.DashboardBody);
    expect(dashboardBody).toContain('\\"Platform/Billing\\"');
    expect(dashboardBody).toContain('\\"TenantId\\",\\"TENANT_ID_PLACEHOLDER\\"');
    expect(dashboardBody).toContain('\\"Tier\\",\\"TENANT_TIER_PLACEHOLDER\\"');
    expect(dashboardBody).toContain('\\"MonthlyCost\\"');
    expect(dashboardBody).toContain('\\"DailyCost\\"');
    expect(dashboardBody).toContain('\\"InputTokens\\"');
    expect(dashboardBody).toContain('\\"OutputTokens\\"');
  });

  test('exposes shared tenant usage dashboard name output', () => {
    const template = synthStack();
    template.hasOutput('TenantUsageDashboardName', {
      Value: Match.stringLikeRegexp('platform-tenant-usage-ObservabilityStack'),
    });
  });

  test('includes both primary and failover AgentCore runtime regions in the dashboard view', () => {
    const template = synthStack();
    const dashboards = template.findResources('AWS::CloudWatch::Dashboard') as Record<
      string,
      { Properties?: { DashboardBody?: unknown } }
    >;
    const dashboard = Object.values(dashboards)[0];
    const dashboardBody = JSON.stringify(dashboard.Properties?.DashboardBody);

    expect(dashboardBody).toContain('AgentCore Runtime (Primary + Failover)');
    expect(dashboardBody).toContain('AWS/BedrockAgentCore');
    expect(dashboardBody).toContain('eu-west-1 ConcurrentSessions');
    expect(dashboardBody).toContain('eu-central-1 ConcurrentSessions');
    expect(dashboardBody).toContain('eu-west-1 ExecutionErrors');
    expect(dashboardBody).toContain('eu-central-1 ExecutionErrors');
  });

  test('creates FM-1 Runtime Region Unavailable alarm', () => {
    const template = synthStack();
    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'ObservabilityStack-FM-1-RuntimeRegionUnavailable',
      ComparisonOperator: 'GreaterThanOrEqualToThreshold',
      Threshold: 5,
    });
  });

  test('creates FM-2 Authoriser Cold Start alarm', () => {
    const template = synthStack();
    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'ObservabilityStack-FM-2-AuthoriserColdStartSpike',
      Threshold: 500,
    });
  });

  test('creates authoriser hard-failure alarm with missing data treated as not breaching', () => {
    const template = synthStack();
    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'ObservabilityStack-Authoriser-HardFailures',
      MetricName: 'Errors',
      Namespace: 'AWS/Lambda',
      Threshold: 1,
      TreatMissingData: 'notBreaching',
    });
  });

  test('creates authoriser failure metric filters for infrastructure-only deny paths', () => {
    const template = synthStack();
    const expectedFilters = [
      {
        pattern: '{ $.message = "Failed to fetch tenant status" }',
        metricName: 'TenantStatusLookupFailureCount',
      },
      {
        pattern: '{ $.message = "JWK client not initialized (ENTRA_JWKS_URL missing)" }',
        metricName: 'JwksClientInitializationFailureCount',
      },
      {
        pattern: '{ $.message = "Unexpected error during JWT validation" }',
        metricName: 'UnexpectedJwtValidationExceptionCount',
      },
      {
        pattern: '{ $.message = "Failed to resolve SigV4 tenant binding via GSI" }',
        metricName: 'SigV4BindingResolutionFailureCount',
      },
    ];

    for (const expectedFilter of expectedFilters) {
      template.hasResourceProperties('AWS::Logs::MetricFilter', {
        FilterPattern: expectedFilter.pattern,
        MetricTransformations: [
          Match.objectLike({
            MetricNamespace: 'Platform/Authoriser',
            MetricName: expectedFilter.metricName,
            MetricValue: '1',
          }),
        ],
      });
    }
  });

  test('creates authoriser infrastructure failure alarms with missing data treated as not breaching', () => {
    const template = synthStack();
    const expectedAlarmNames = [
      'ObservabilityStack-Authoriser-TenantStatusLookupFailure',
      'ObservabilityStack-Authoriser-JwksClientInitializationFailure',
      'ObservabilityStack-Authoriser-UnexpectedJwtValidationException',
      'ObservabilityStack-Authoriser-SigV4BindingResolutionFailure',
    ];

    for (const alarmName of expectedAlarmNames) {
      template.hasResourceProperties('AWS::CloudWatch::Alarm', {
        AlarmName: alarmName,
        Namespace: 'Platform/Authoriser',
        Threshold: 1,
        TreatMissingData: 'notBreaching',
      });
    }
  });

  test('creates FM-3 Secrets Manager Throttling alarm', () => {
    const template = synthStack();
    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'ObservabilityStack-FM-3-SecretsManagerThrottling',
      MetricName: 'SecretsManagerCacheMissCount',
    });
  });

  test('creates FM-4 DynamoDB Hot Partition alarm', () => {
    const template = synthStack();
    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'ObservabilityStack-FM-4-DynamoDbHotPartition',
      MetricName: 'ThrottledRequests',
    });
  });

  test('creates FM-5 Bridge Timeout alarm', () => {
    const template = synthStack();
    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'ObservabilityStack-FM-5-BridgeTimeout',
    });
  });

  test('creates FM-6 Interceptor Retry Storm alarm', () => {
    const template = synthStack();
    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'ObservabilityStack-FM-6-InterceptorRetryStorm',
    });
  });

  test('creates FM-7 AgentCore Memory Degraded alarm', () => {
    const template = synthStack();
    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'ObservabilityStack-FM-7-AgentCoreMemoryDegraded',
      Namespace: 'AWS/BedrockAgentCore',
      MetricName: 'DegradedMode',
    });
  });

  test('creates FM-11 Bedrock throttle pressure alarm', () => {
    const template = synthStack();
    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'ObservabilityStack-FM-11-BedrockThrottlePressure',
      MetricName: 'Invocation.Throttled.Bedrock',
      Threshold: 1,
    });
  });

  test('creates FM-12 Valkey unavailable alarm', () => {
    const template = synthStack();
    template.hasResourceProperties('AWS::Logs::MetricFilter', {
      FilterPattern: '{ $.event.name = "valkey_unavailable" }',
      MetricTransformations: [
        Match.objectLike({
          MetricNamespace: 'Platform/Bridge',
          MetricName: 'ValkeyUnavailableCount',
          MetricValue: '1',
        }),
      ],
    });

    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'ObservabilityStack-FM-12-ValkeyUnavailable',
      Namespace: 'Platform/Bridge',
      MetricName: 'ValkeyUnavailableCount',
      ComparisonOperator: 'GreaterThanOrEqualToThreshold',
      Threshold: 1,
      TreatMissingData: 'notBreaching',
    });
  });

  test('creates FM-8 Usage Plan Quota Exhausted alarm', () => {
    const template = synthStack();
    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'ObservabilityStack-FM-8-UsagePlanQuotaExhausted',
    });
  });

  test('creates FM-9 DLQ Arrival alarms for each DLQ', () => {
    const template = synthStack();
    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: Match.stringLikeRegexp('ObservabilityStack-FM-9-DLQ-Arrival-'),
    });
  });

  test('creates FM-10 Billing Lambda Failure alarm', () => {
    const template = synthStack();
    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'ObservabilityStack-FM-10-BillingLambdaFailure',
    });
  });

  test('creates Cross-Region Observability Sink', () => {
    const template = synthStack();
    template.resourceCountIs('AWS::Oam::Sink', 1);
    template.hasResourceProperties('AWS::Oam::Sink', {
      Name: 'PlatformObservabilitySink',
    });
  });

  test('exposes sink-only observability topology and no OAM links yet', () => {
    const template = synthStack();
    template.resourceCountIs('AWS::Oam::Link', 0);
    template.hasOutput('CrossRegionObservabilityTopology', {
      Value: 'SINK_ONLY_NO_OAM_LINKS',
    });
  });

  test('creates WAF and CloudFront alarms', () => {
    const template = synthStack();
    const api5xxAlarmName = ['ObservabilityStack', 'Platform', 'API', '5xx', 'Errors'].join('-');
    const wafBlockedAlarmName = [
      'ObservabilityStack',
      'Platform',
      'WAF',
      'Blocked',
      'Requests',
    ].join('-');

    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: api5xxAlarmName,
    });
    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: wafBlockedAlarmName,
      Namespace: 'AWS/WAFV2',
      MetricName: 'BlockedRequests',
      Dimensions: Match.arrayWith([
        Match.objectLike({
          Name: 'Region',
          Value: 'eu-west-2',
        }),
        Match.objectLike({
          Name: 'Rule',
          Value: 'ALL',
        }),
        Match.objectLike({
          Name: 'WebACL',
          Value: 'PlatformStack-api-waf',
        }),
      ]),
    });
  });

  test('every documented FM (1-10) has a corresponding alarm', () => {
    const template = synthStack();
    const documentedFMs = [
      'FM-1-RuntimeRegionUnavailable',
      'FM-2-AuthoriserColdStartSpike',
      'FM-3-SecretsManagerThrottling',
      'FM-4-DynamoDbHotPartition',
      'FM-5-BridgeTimeout',
      'FM-6-InterceptorRetryStorm',
      'FM-7-AgentCoreMemoryDegraded',
      'FM-11-BedrockThrottlePressure',
      'FM-12-ValkeyUnavailable',
      'FM-8-UsagePlanQuotaExhausted',
      'FM-9-DLQ-Arrival-',
      'FM-10-BillingLambdaFailure',
    ];
    for (const fm of documentedFMs) {
      template.hasResourceProperties('AWS::CloudWatch::Alarm', {
        AlarmName: Match.stringLikeRegexp(`ObservabilityStack-${fm}`),
      });
    }
  });
});
