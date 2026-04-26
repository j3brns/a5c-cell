import * as cdk from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import { PlatformStack } from '../lib/platform-stack';

describe('PlatformStack (TASK-023)', () => {
  const synthTemplate = (
    environment: 'dev' | 'staging' | 'prod' = 'dev',
    extraContext: Record<string, string> = {},
  ) => {
    const app = new cdk.App({
      context: {
        env: environment,
        entraTenantId: '00000000-0000-0000-0000-000000000000',
        ...extraContext,
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

    const stack = new PlatformStack(app, `platform-core-${environment}`, {
      env,
      vpc: mockVpc,
      lambdaSecurityGroup,
    });
    return Template.fromStack(stack);
  };
  const template = synthTemplate('dev');

  const getSpaContentSecurityPolicy = (stackTemplate: Template) => {
    const policies = stackTemplate.findResources('AWS::CloudFront::ResponseHeadersPolicy') as Record<
      string,
      {
        Properties?: {
          ResponseHeadersPolicyConfig?: {
            SecurityHeadersConfig?: {
              ContentSecurityPolicy?: {
                ContentSecurityPolicy?: unknown;
              };
            };
          };
        };
      }
    >;

    const [policy] = Object.values(policies);
    return policy?.Properties?.ResponseHeadersPolicyConfig?.SecurityHeadersConfig?.ContentSecurityPolicy
      ?.ContentSecurityPolicy;
  };

  const getWebAclRules = (stackTemplate: Template): Array<{
    Name: string;
    Priority: number;
    VisibilityConfig?: {
      MetricName?: string;
      SampledRequestsEnabled?: boolean;
    };
  }> => {
    const webAcls = stackTemplate.findResources('AWS::WAFv2::WebACL') as Record<
      string,
      {
        Properties?: {
          Rules?: Array<{
            Name: string;
            Priority: number;
            VisibilityConfig?: {
              MetricName?: string;
              SampledRequestsEnabled?: boolean;
            };
          }>;
        };
      }
    >;

    const [webAcl] = Object.values(webAcls);
    return webAcl.Properties?.Rules ?? [];
  };

  const getIamPolicyStatements = (stackTemplate: Template): Array<Record<string, unknown>> => {
    const policies = stackTemplate.findResources('AWS::IAM::Policy') as Record<
      string,
      { Properties?: { PolicyDocument?: { Statement?: Array<Record<string, unknown>> } } }
    >;

    return Object.values(policies).flatMap((resource) => resource.Properties?.PolicyDocument?.Statement ?? []);
  };

  const getBridgeAssumeRoleStatements = (stackTemplate: Template): Array<Record<string, unknown>> =>
    getIamPolicyStatements(stackTemplate).filter((statement) => statement.Action === 'sts:AssumeRole');

  const getScopedPutMetricStatements = (
    stackTemplate: Template,
    rolePattern?: string,
  ): Array<Record<string, unknown>> => {
    const policies = stackTemplate.findResources('AWS::IAM::Policy') as Record<
      string,
      {
        Properties?: {
          PolicyDocument?: { Statement?: Array<Record<string, unknown>> };
          Roles?: Array<unknown>;
        };
      }
    >;

    return Object.values(policies)
      .filter((resource) => {
        if (!rolePattern) {
          return true;
        }

        return (resource.Properties?.Roles ?? []).some((role) =>
          JSON.stringify(role).match(new RegExp(rolePattern)),
        );
      })
      .flatMap((resource) => resource.Properties?.PolicyDocument?.Statement ?? [])
      .filter((statement) => {
        const action = statement.Action;
        return action === 'cloudwatch:PutMetricData';
      });
  };

  test('creates all required DynamoDB tables with on-demand billing, PITR, and encryption', () => {
    template.resourceCountIs('AWS::DynamoDB::Table', 8);

    const tableNames = [
      'platform-tenants',
      'platform-agents',
      'platform-tools',
      'platform-ops-locks',
      'platform-gateway-idempotency',
      'platform-invocations',
      'platform-jobs',
      'platform-sessions',
    ];

    for (const tableName of tableNames) {
      template.hasResourceProperties('AWS::DynamoDB::Table', {
        TableName: tableName,
        BillingMode: 'PAY_PER_REQUEST',
        PointInTimeRecoverySpecification: {
          PointInTimeRecoveryEnabled: true,
        },
      });
    }

    template.hasResourceProperties('AWS::DynamoDB::Table', {
      TableName: 'platform-invocations',
      BillingMode: 'PAY_PER_REQUEST',
      TimeToLiveSpecification: {
        AttributeName: 'ttl',
        Enabled: true,
      },
    });

    template.hasResourceProperties('AWS::DynamoDB::Table', {
      TableName: 'platform-jobs',
      StreamSpecification: {
        StreamViewType: 'NEW_AND_OLD_IMAGES',
      },
    });

    const tables = template.findResources('AWS::DynamoDB::Table') as Record<
      string,
      { Properties?: { SSESpecification?: Record<string, unknown>; ProvisionedThroughput?: unknown } }
    >;
    for (const table of Object.values(tables)) {
      expect(table.Properties?.SSESpecification).toEqual({
        SSEEnabled: true,
      });
      expect(table.Properties?.ProvisionedThroughput).toBeUndefined();
    }
  });

  test('creates REST API with authorizer-backed API key source and usage plans', () => {
    template.hasResourceProperties('AWS::ApiGateway::RestApi', {
      ApiKeySourceType: 'AUTHORIZER', // pragma: allowlist secret
    });

    template.resourceCountIs('AWS::ApiGateway::UsagePlan', 3);
    template.resourceCountIs('AWS::Lambda::Alias', 2);

    template.hasResourceProperties('AWS::Lambda::Alias', {
      Name: 'live',
      ProvisionedConcurrencyConfig: {
        ProvisionedConcurrentExecutions: 10,
      },
    });
  });

  test('attaches the SPA CloudFront web ACL when spaWebAclArn context is provided', () => {
    const template = synthTemplate('dev', {
      spaWebAclArn:
        'arn:aws:wafv2:us-east-1:123456789012:global/webacl/platform-edge-security-dev-spa-edge-waf/11111111-1111-1111-1111-111111111111',
    });

    template.hasResourceProperties('AWS::CloudFront::Distribution', {
      DistributionConfig: Match.objectLike({
        WebACLId:
          'arn:aws:wafv2:us-east-1:123456789012:global/webacl/platform-edge-security-dev-spa-edge-waf/11111111-1111-1111-1111-111111111111',
      }),
    });
  });

  test('wires canonical invoke and jobs routes and removes legacy /v1/invoke', () => {
    const resources = template.findResources('AWS::ApiGateway::Resource');
    const pathParts = Object.values(resources).map((resource) => {
      const properties = (resource as { Properties?: { PathPart?: string } }).Properties;
      return properties?.PathPart;
    });

    expect(pathParts).toContain('agents');
    expect(pathParts).toContain('{agentName}');
    expect(pathParts).toContain('invoke');
    expect(pathParts).toContain('jobs');
    expect(pathParts).toContain('{jobId}');

    const stages = template.findResources('AWS::ApiGateway::Stage');
    const methodSettings = Object.values(stages).flatMap((stage) => {
      const properties = (stage as { Properties?: { MethodSettings?: Array<unknown> } }).Properties;
      return properties?.MethodSettings ?? [];
    });

    expect(methodSettings).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          HttpMethod: 'POST',
          ResourcePath: '/~1v1~1agents~1{agentName}~1invoke',
        }),
        expect.objectContaining({
          HttpMethod: 'GET',
          ResourcePath: '/~1v1~1jobs~1{jobId}',
        }),
      ]),
    );
    expect(methodSettings).not.toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          HttpMethod: 'POST',
          ResourcePath: '/~1v1~1invoke',
        }),
      ]),
    );
  });

  test('exposes documented webhook and tenant invite listing routes', () => {
    const resources = template.findResources('AWS::ApiGateway::Resource');
    const pathParts = Object.values(resources).map((resource) => {
      const properties = (resource as { Properties?: { PathPart?: string } }).Properties;
      return properties?.PathPart;
    });

    expect(pathParts).toContain('webhooks');
    expect(pathParts).toContain('invites');

    const methods = template.findResources('AWS::ApiGateway::Method') as Record<
      string,
      { Properties?: { HttpMethod?: string; ResourceId?: unknown } }
    >;
    const resourceEntries = Object.entries(resources);
    const webhookLogicalId = resourceEntries.find(
      ([, resource]) =>
        (resource as { Properties?: { PathPart?: string } }).Properties?.PathPart === 'webhooks',
    )?.[0];
    const invitesLogicalId = resourceEntries.find(
      ([, resource]) =>
        (resource as { Properties?: { PathPart?: string } }).Properties?.PathPart === 'invites',
    )?.[0];

    expect(webhookLogicalId).toBeDefined();
    expect(invitesLogicalId).toBeDefined();

    expect(Object.values(methods)).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          Properties: expect.objectContaining({
            HttpMethod: 'GET',
            ResourceId: { Ref: webhookLogicalId },
          }),
        }),
        expect.objectContaining({
          Properties: expect.objectContaining({
            HttpMethod: 'GET',
            ResourceId: { Ref: invitesLogicalId },
          }),
        }),
      ]),
    );
  });

  test('deploys control-plane Lambdas outside the VPC unless they have a private dependency', () => {
    const template = synthTemplate('dev');

    const lambdaFunctions = template.findResources('AWS::Lambda::Function') as Record<
      string,
      {
        Properties?: {
          FunctionName?: string;
          VpcConfig?: unknown;
        };
      }
    >;

    for (const resource of Object.values(lambdaFunctions)) {
      expect(resource.Properties?.VpcConfig).toBeUndefined();
    }
  });

  test('creates environment-aware bridge rollout policy with auto-rollback alarm', () => {
    const devTemplate = synthTemplate('dev');
    const stagingTemplate = synthTemplate('staging');
    const prodTemplate = synthTemplate('prod');

    devTemplate.hasResourceProperties('AWS::CodeDeploy::DeploymentGroup', {
      DeploymentConfigName: 'CodeDeployDefault.LambdaAllAtOnce',
    });
    stagingTemplate.hasResourceProperties('AWS::CodeDeploy::DeploymentGroup', {
      DeploymentConfigName: 'CodeDeployDefault.LambdaCanary10Percent30Minutes',
    });
    prodTemplate.hasResourceProperties('AWS::CodeDeploy::DeploymentGroup', {
      DeploymentConfigName: 'CodeDeployDefault.LambdaCanary10Percent15Minutes',
    });

    stagingTemplate.hasOutput('BridgeCanaryPolicy', {
      Value: 'staging=canary-10%-30m',
    });
    prodTemplate.hasOutput('BridgeCanaryPolicy', {
      Value: 'prod=canary-10%-15m',
    });

    prodTemplate.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'platform-core-prod-error_rate_high',
      ComparisonOperator: 'GreaterThanOrEqualToThreshold',
      Threshold: 5,
    });
    prodTemplate.hasResourceProperties('AWS::CodeDeploy::DeploymentGroup', {
      AutoRollbackConfiguration: {
        Enabled: true,
        Events: Match.arrayWith([
          'DEPLOYMENT_FAILURE',
          'DEPLOYMENT_STOP_ON_REQUEST',
          'DEPLOYMENT_STOP_ON_ALARM',
        ]),
      },
      AlarmConfiguration: Match.objectLike({
        Enabled: true,
      }),
    });
  });

  test('creates AppConfig validator, bounded deployment strategy, and initial deployment', () => {
    const devTemplate = synthTemplate('dev');
    const prodTemplate = synthTemplate('prod');

    devTemplate.hasResourceProperties('AWS::AppConfig::ConfigurationProfile', {
      Name: 'tenant-capabilities',
      Validators: Match.arrayWith([
        Match.objectLike({
          Type: 'JSON_SCHEMA',
        }),
      ]),
    });

    devTemplate.hasResourceProperties('AWS::AppConfig::DeploymentStrategy', {
      GrowthType: 'LINEAR',
      GrowthFactor: 50,
      DeploymentDurationInMinutes: 10,
      FinalBakeTimeInMinutes: 5,
    });

    prodTemplate.hasResourceProperties('AWS::AppConfig::DeploymentStrategy', {
      GrowthType: 'LINEAR',
      GrowthFactor: 25,
      DeploymentDurationInMinutes: 30,
      FinalBakeTimeInMinutes: 15,
    });

    devTemplate.resourceCountIs('AWS::AppConfig::Deployment', 1);
  });

  test('creates WAF WebACL with managed baselines, rate limits, and API association', () => {
    const rules = getWebAclRules(template);

    expect(rules.map((rule) => rule.Name)).toEqual([
      'AWSManagedRulesCommonRuleSet',
      'AWSManagedRulesAmazonIpReputationList',
      'AWSManagedRulesKnownBadInputsRuleSet',
      'GlobalIpRateLimit',
      'UkIpRateLimit',
      'BlockSqlmapUserAgent',
    ]);
    expect(rules.map((rule) => rule.Priority)).toEqual([0, 1, 2, 3, 4, 5]);
    expect(rules.map((rule) => rule.VisibilityConfig?.MetricName)).toEqual([
      'aws-managed-common',
      'aws-managed-amazon-ip-reputation-count',
      'aws-managed-known-bad-inputs-count',
      'global-ip-rate-limit',
      'uk-ip-rate-limit',
      'block-sqlmap-user-agent',
    ]);
    expect(rules.every((rule) => rule.VisibilityConfig?.SampledRequestsEnabled === true)).toBe(true);

    template.hasResourceProperties('AWS::WAFv2::WebACL', {
      Scope: 'REGIONAL',
      VisibilityConfig: Match.objectLike({
        SampledRequestsEnabled: true,
      }),
      Rules: Match.arrayWith([
        Match.objectLike({
          Name: 'AWSManagedRulesCommonRuleSet',
          Statement: Match.objectLike({
            ManagedRuleGroupStatement: {
              VendorName: 'AWS',
              Name: 'AWSManagedRulesCommonRuleSet',
            },
          }),
        }),
        Match.objectLike({
          Name: 'AWSManagedRulesAmazonIpReputationList',
          OverrideAction: { Count: {} },
          Statement: Match.objectLike({
            ManagedRuleGroupStatement: {
              VendorName: 'AWS',
              Name: 'AWSManagedRulesAmazonIpReputationList',
            },
          }),
        }),
        Match.objectLike({
          Name: 'AWSManagedRulesKnownBadInputsRuleSet',
          OverrideAction: { Count: {} },
          Statement: Match.objectLike({
            ManagedRuleGroupStatement: {
              VendorName: 'AWS',
              Name: 'AWSManagedRulesKnownBadInputsRuleSet',
            },
          }),
        }),
        Match.objectLike({
          Name: 'GlobalIpRateLimit',
          Statement: Match.objectLike({
            RateBasedStatement: Match.objectLike({
              AggregateKeyType: 'IP',
              Limit: 10000,
            }),
          }),
        }),
        Match.objectLike({
          Name: 'UkIpRateLimit',
          Statement: Match.objectLike({
            RateBasedStatement: Match.objectLike({
              AggregateKeyType: 'IP',
              Limit: 2000,
              ScopeDownStatement: Match.objectLike({
                GeoMatchStatement: {
                  CountryCodes: ['GB'],
                },
              }),
            }),
          }),
        }),
        Match.objectLike({
          Name: 'BlockSqlmapUserAgent',
        }),
      ]),
    });

    template.resourceCountIs('AWS::WAFv2::WebACLAssociation', 1);
  });

  test('keeps CloudFront WebACL wiring optional when no spaWebAclArn context is provided', () => {
    template.resourceCountIs('AWS::WAFv2::WebACL', 1);

    const distributions = template.findResources('AWS::CloudFront::Distribution');
    expect(Object.keys(distributions)).toHaveLength(1);

    const [distribution] = Object.values(distributions) as Array<{
      Properties?: { DistributionConfig?: Record<string, unknown> };
    }>;

    expect(distribution.Properties?.DistributionConfig).not.toHaveProperty('WebACLId');
  });

  test('creates CloudFront distribution with OAC and CSP response headers policy', () => {
    template.resourceCountIs('AWS::CloudFront::OriginAccessControl', 1);

    template.hasResourceProperties('AWS::CloudFront::ResponseHeadersPolicy', {
      ResponseHeadersPolicyConfig: Match.objectLike({
        SecurityHeadersConfig: Match.objectLike({
          ContentSecurityPolicy: Match.objectLike({
            Override: true,
          }),
          FrameOptions: Match.objectLike({
            FrameOption: 'DENY',
            Override: true,
          }),
          StrictTransportSecurity: Match.objectLike({
            AccessControlMaxAgeSec: 31536000,
            IncludeSubdomains: true,
            Preload: true,
            Override: true,
          }),
        }),
      }),
    });

    template.hasResourceProperties('AWS::CloudFront::Distribution', {
      DistributionConfig: Match.objectLike({
        Origins: Match.arrayWith([
          Match.objectLike({
            OriginAccessControlId: Match.anyValue(),
          }),
        ]),
        DefaultCacheBehavior: Match.objectLike({
          ResponseHeadersPolicyId: Match.anyValue(),
        }),
      }),
    });
  });

  test('builds CloudFront connect-src from explicit non-production origins for the default stack', () => {
    const contentSecurityPolicy = JSON.stringify(getSpaContentSecurityPolicy(template));

    expect(contentSecurityPolicy).toContain('login.microsoftonline.com');
    expect(contentSecurityPolicy).toContain('localhost:3000');
    expect(contentSecurityPolicy).toContain('localhost:4566');
    expect(contentSecurityPolicy).toContain('localhost:8080');
    expect(contentSecurityPolicy).not.toMatch(/connect-src\s+'self'\s+https:\s*;/);
    expect(String(getSpaContentSecurityPolicy(template)).length).toBeLessThan(1784);
  });

  test('configures CloudFront route fallback without masking missing asset failures', () => {
    template.resourceCountIs('AWS::CloudFront::Function', 1);

    template.hasResourceProperties('AWS::CloudFront::Distribution', {
      DistributionConfig: Match.objectLike({
        DefaultCacheBehavior: Match.objectLike({
          FunctionAssociations: Match.arrayWith([
            Match.objectLike({
              EventType: 'viewer-request',
              FunctionARN: Match.anyValue(),
            }),
          ]),
        }),
      }),
    });

    const distributions = template.findResources('AWS::CloudFront::Distribution');
    const [distribution] = Object.values(distributions) as Array<{
      Properties?: { DistributionConfig?: Record<string, unknown> };
    }>;
    expect(distribution.Properties?.DistributionConfig).not.toHaveProperty('CustomErrorResponses');
  });

  test('distinguishes SPA shell caching from immutable asset caching', () => {
    template.hasResourceProperties('AWS::CloudFront::Distribution', {
      DistributionConfig: Match.objectLike({
        DefaultCacheBehavior: Match.objectLike({
          CachePolicyId: cloudfront.CachePolicy.CACHING_DISABLED.cachePolicyId,
        }),
        CacheBehaviors: Match.arrayWith([
          Match.objectLike({
            PathPattern: 'assets/*',
            CachePolicyId: cloudfront.CachePolicy.CACHING_OPTIMIZED.cachePolicyId,
          }),
        ]),
      }),
    });
  });

  test('configures API Gateway CORS preflight to CloudFront origin only', () => {
    const optionsMethods = template.findResources('AWS::ApiGateway::Method', {
      Properties: {
        HttpMethod: 'OPTIONS',
      },
    });

    expect(Object.keys(optionsMethods).length).toBeGreaterThan(0);

    for (const method of Object.values(optionsMethods) as Array<{ Properties?: unknown }>) {
      const properties = method.Properties as {
        Integration?: { IntegrationResponses?: Array<{ ResponseParameters?: Record<string, unknown> }> };
      };
      const responseParameters =
        properties.Integration?.IntegrationResponses?.[0]?.ResponseParameters ?? {};
      const allowOrigin = responseParameters['method.response.header.Access-Control-Allow-Origin'];
      const allowMethods = responseParameters['method.response.header.Access-Control-Allow-Methods'];

      expect(allowOrigin).toBeDefined();
      expect(JSON.stringify(allowOrigin)).toContain('DomainName');
      expect(JSON.stringify(allowOrigin)).not.toContain("'*'");
      expect(JSON.stringify(allowMethods)).toContain('OPTIONS');
    }
  });

  test('creates AgentCore Gateway with request and response interceptor wiring', () => {
    template.hasResourceProperties('AWS::BedrockAgentCore::Gateway', {
      AuthorizerType: 'AWS_IAM',
      ProtocolType: 'MCP',
      PolicyEngineConfiguration: Match.objectLike({
        Arn: Match.anyValue(),
        Mode: 'LOG_ONLY',
      }),
      InterceptorConfigurations: Match.arrayWith([
        Match.objectLike({
          InterceptionPoints: ['REQUEST'],
          InputConfiguration: {
            PassRequestHeaders: true,
          },
          Interceptor: Match.objectLike({
            Lambda: Match.objectLike({
              Arn: Match.anyValue(),
            }),
          }),
        }),
        Match.objectLike({
          InterceptionPoints: ['RESPONSE'],
          InputConfiguration: {
            PassRequestHeaders: true,
          },
        }),
      ]),
    });
  });

  test('creates AgentCore Policy Engine and Cedar policy resources', () => {
    template.resourceCountIs('AWS::BedrockAgentCore::PolicyEngine', 1);
    template.resourceCountIs('AWS::BedrockAgentCore::Policy', 1);

    template.hasResourceProperties('AWS::BedrockAgentCore::PolicyEngine', {
      Name: 'PlatformGatewayPolicyEngineDev',
    });

    template.hasResourceProperties('AWS::BedrockAgentCore::Policy', {
      Name: 'PlatformGatewayTenantRoleAccessDev',
      ValidationMode: 'FAIL_ON_ANY_FINDINGS',
      Definition: Match.objectLike({
        Cedar: Match.objectLike({
          Statement: Match.anyValue(),
        }),
      }),
    });

    const policies = template.findResources('AWS::BedrockAgentCore::Policy');
    const policy = Object.values(policies)[0] as {
      Properties?: { Definition?: { Cedar?: { Statement?: unknown } } };
    };
    const rawStatement = policy.Properties?.Definition?.Cedar?.Statement;
    const fnSub = (rawStatement as { 'Fn::Sub'?: string | [string, unknown] } | undefined)?.['Fn::Sub'];
    const statement =
      typeof rawStatement === 'string' ? rawStatement : Array.isArray(fnSub) ? fnSub[0] : String(fnSub ?? '');
    expect(statement).toContain('principal is AgentCore::IamEntity');
    expect(statement).toContain('resource == AgentCore::Gateway::');
    expect(statement).toContain('platform-tenant-*-execution-role');
    expect(statement).not.toContain('when {\n  true\n}');

    template.hasOutput('AgentCoreGatewayPolicyMode', {
      Value: 'LOG_ONLY',
    });
  });

  test('uses LOG_ONLY for non-prod and ENFORCE for prod gateway policy mode', () => {
    const stagingTemplate = synthTemplate('staging');
    const prodTemplate = synthTemplate('prod');

    stagingTemplate.hasResourceProperties('AWS::BedrockAgentCore::Gateway', {
      PolicyEngineConfiguration: Match.objectLike({
        Mode: 'LOG_ONLY',
      }),
    });
    prodTemplate.hasResourceProperties('AWS::BedrockAgentCore::Gateway', {
      PolicyEngineConfiguration: Match.objectLike({
        Mode: 'ENFORCE',
      }),
    });
  });

  test('grants gateway role policy-engine authorization actions without wildcard resource', () => {
    const policies = template.findResources('AWS::IAM::Policy');
    const allStatements = Object.values(policies).flatMap((resource) => {
      const properties = (resource as { Properties?: { PolicyDocument?: { Statement?: Array<Record<string, unknown>> } } })
        .Properties;
      return properties?.PolicyDocument?.Statement ?? [];
    });

    const getPolicyEngineStatement = allStatements.find((statement) => {
      const actions = Array.isArray(statement.Action) ? statement.Action : [statement.Action];
      return actions.length === 1 && actions.includes('bedrock-agentcore:GetPolicyEngine');
    });
    const authorizationStatement = allStatements.find((statement) => {
      const actions = Array.isArray(statement.Action) ? statement.Action : [statement.Action];
      return (
        actions.includes('bedrock-agentcore:AuthorizeAction') &&
        actions.includes('bedrock-agentcore:PartiallyAuthorizeActions')
      );
    });

    expect(getPolicyEngineStatement).toBeDefined();
    expect(authorizationStatement).toBeDefined();

    for (const statement of [getPolicyEngineStatement, authorizationStatement]) {
      const resources = Array.isArray(statement?.Resource) ? statement?.Resource : [statement?.Resource];
      expect(resources).not.toContain('*');
    }
    const authorizationResources = Array.isArray(authorizationStatement?.Resource)
      ? authorizationStatement?.Resource
      : [authorizationStatement?.Resource];
    expect(authorizationResources).toEqual(expect.arrayContaining([expect.objectContaining({ 'Fn::GetAtt': expect.any(Array) })]));
  });

  test('does not synthesize a standalone async-runner lambda', () => {
    const functions = template.findResources('AWS::Lambda::Function');
    const names = Object.values(functions).map((resource) => {
      const properties = (resource as { Properties?: { FunctionName?: string } }).Properties;
      return String(properties?.FunctionName ?? '');
    });

    expect(names.some((name) => name.includes('async-runner'))).toBe(false);
  });

  test('provisions webhook delivery lambda with jobs stream and retry queue wiring', () => {
    template.hasResourceProperties('AWS::Lambda::Function', {
      FunctionName: 'platform-core-dev-webhook-delivery',
      Handler: 'handler.handler',
      Environment: {
        Variables: Match.objectLike({
          JOBS_TABLE: Match.anyValue(),
          WEBHOOK_MAX_RETRY_ATTEMPTS: '3',
        }),
      },
    });

    template.hasResourceProperties('AWS::Lambda::EventSourceMapping', {
      StartingPosition: 'LATEST',
      BatchSize: 10,
    });

    template.hasResourceProperties('AWS::SQS::Queue', {
      RedrivePolicy: Match.objectLike({
        maxReceiveCount: 1,
      }),
    });
  });

  test('splits tenant control-plane routes across dedicated service lambdas', () => {
    const lambdaFunctions = template.findResources('AWS::Lambda::Function') as Record<
      string,
      {
        Properties?: {
          FunctionName?: string;
          Handler?: string;
          Environment?: { Variables?: Record<string, unknown> };
        };
      }
    >;

    const tenantMgmtLambda = Object.values(lambdaFunctions).find(
      (resource) => resource.Properties?.FunctionName === 'platform-core-dev-tenant-mgmt',
    );

    expect(tenantMgmtLambda?.Properties?.Handler).toBe('tenant_mgmt_handler.lambda_handler');
    expect(tenantMgmtLambda?.Properties?.Environment?.Variables).toEqual(
      expect.objectContaining({
        POWERTOOLS_SERVICE_NAME: 'tenant-mgmt-service',
        TENANTS_TABLE_NAME: expect.anything(),
        INVOCATIONS_TABLE_NAME: expect.anything(),
        AUDIT_EXPORT_BUCKET: {
          Ref: 'ResultsBucketA95A2103',
        },
        TENANT_API_KEY_SECRET_PREFIX: 'platform/tenants', // pragma: allowlist secret
      }),
    );

    template.hasResourceProperties('AWS::IAM::Policy', {
      PolicyDocument: {
        Statement: Match.arrayWith([
          Match.objectLike({
            Action: 's3:ListBucket',
            Resource: {
              'Fn::GetAtt': ['ResultsBucketA95A2103', 'Arn'],
            },
            Condition: {
              StringLike: {
                's3:prefix': ['tenants/*'],
              },
            },
          }),
          Match.objectLike({
            Action: ['s3:GetObject', 's3:PutObject', 's3:DeleteObject'],
            Resource: {
              'Fn::Join': [
                '',
                [
                  {
                    'Fn::GetAtt': ['ResultsBucketA95A2103', 'Arn'],
                  },
                  '/tenants/*',
                ],
              ],
            },
          }),
        ]),
      },
    });

    template.hasResourceProperties('AWS::Lambda::Function', {
      FunctionName: 'platform-core-dev-webhook-registry',
      Handler: 'webhook_registry_handler.lambda_handler',
      Environment: {
        Variables: Match.objectLike({
          POWERTOOLS_SERVICE_NAME: 'webhook-registry-service',
          TENANTS_TABLE_NAME: Match.anyValue(),
        }),
      },
    });

    template.hasResourceProperties('AWS::Lambda::Function', {
      FunctionName: 'platform-core-dev-agent-registry',
      Handler: 'agent_registry_handler.lambda_handler',
      Environment: {
        Variables: Match.objectLike({
          POWERTOOLS_SERVICE_NAME: 'agent-registry-service',
          AGENTS_TABLE_NAME: Match.anyValue(),
        }),
      },
    });

    template.hasResourceProperties('AWS::Lambda::Function', {
      FunctionName: 'platform-core-dev-admin-ops',
      Handler: 'admin_ops_handler.lambda_handler',
      Environment: {
        Variables: Match.objectLike({
          POWERTOOLS_SERVICE_NAME: 'admin-ops-service',
          TENANTS_TABLE_NAME: Match.anyValue(),
          OPS_LOCKS_TABLE: Match.anyValue(),
          FAILOVER_LOCK_NAME: 'platform-runtime-failover',
        }),
      },
    });

    template.hasResourceProperties('AWS::Lambda::Function', {
      FunctionName: 'platform-core-dev-bridge',
      Handler: 'handler.handler',
      Environment: {
        Variables: Match.objectLike({
          OPS_LOCKS_TABLE: Match.anyValue(),
          FAILOVER_LOCK_NAME: 'platform-runtime-failover',
          RUNTIME_REGION_PARAM: '/platform/config/runtime-region',
          TENANT_EXECUTION_ROLE_PARAM_TEMPLATE: '/platform/tenants/{tenant_id}/execution-role-arn',
          VALKEY_ENDPOINT: Match.anyValue(),
        }),
      },
    });

    template.hasResourceProperties('AWS::IAM::Policy', {
      PolicyDocument: {
        Statement: Match.arrayWith([
          Match.objectLike({
            Action: 'ssm:GetParameter',
            Effect: 'Allow',
            Resource: 'arn:aws:ssm:eu-west-2:123456789012:parameter/platform/tenants/*/execution-role-arn',
          }),
          Match.objectLike({
            Action: 'sts:AssumeRole',
            Effect: 'Allow',
            Resource: 'arn:aws:iam::123456789012:role/platform-tenant-*-execution-role',
          }),
        ]),
      },
      Roles: Match.arrayWith([
        {
          Ref: Match.stringLikeRegexp('bridgeLambdaServiceRole'),
        },
      ]),
    });
  });

  test('constrains Bridge tenant execution-role assumption to the stack account', () => {
    const assumeRoleStatements = getBridgeAssumeRoleStatements(template);

    expect(assumeRoleStatements).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          Effect: 'Allow',
          Resource: 'arn:aws:iam::123456789012:role/platform-tenant-*-execution-role',
        }),
      ]),
    );
    for (const statement of assumeRoleStatements) {
      expect(JSON.stringify(statement.Resource)).not.toContain('arn:aws:iam::*:role/');
    }
  });

  test('configures the BFF lambda with canonical Entra config and secret references', () => {
    template.hasResourceProperties('AWS::Lambda::Function', {
      FunctionName: 'platform-core-dev-bff',
      Environment: {
        Variables: Match.objectLike({
          ENTRA_TENANT_ID: '00000000-0000-0000-0000-000000000000',
          ENTRA_AUDIENCE: 'platform-api',
          ENTRA_TOKEN_ENDPOINT:
            'https://login.microsoftonline.com/00000000-0000-0000-0000-000000000000/oauth2/v2.0/token',
          ENTRA_CLIENT_ID_SECRET_ARN:
            'arn:aws:secretsmanager:eu-west-2:123456789012:secret:platform/dev/entra/client-id',
          ENTRA_CLIENT_SECRET_SECRET_ARN:
            'arn:aws:secretsmanager:eu-west-2:123456789012:secret:platform/dev/entra/client-secret',
          POWERTOOLS_SERVICE_NAME: 'bff',
        }),
      },
    });
  });

  test('scopes AppConfig retrieval permissions to deployed configuration resources', () => {
    const appConfigStatements = getIamPolicyStatements(template).filter((statement) =>
      JSON.stringify(statement.Action).includes('StartConfigurationSession'),
    );

    expect(appConfigStatements.length).toBeGreaterThanOrEqual(3);
    for (const statement of appConfigStatements) {
      const resources = JSON.stringify(statement.Resource);
      expect(resources).toContain('/environment/');
      expect(resources).toContain('/configuration/');
      expect(resources).not.toContain('/configurationprofile/');
    }
  });

  test('wires response interceptor tool filtering and PII pattern permissions', () => {
    template.hasResourceProperties('AWS::Lambda::Function', {
      FunctionName: 'platform-core-dev-interceptor-response',
      Environment: {
        Variables: Match.objectLike({
          TOOLS_TABLE: Match.anyValue(),
          PII_PATTERNS_PARAM: '/platform/gateway/pii-patterns/default',
        }),
      },
    });

    const policyJson = JSON.stringify(template.findResources('AWS::IAM::Policy'));
    expect(policyJson).toContain('parameter/platform/gateway/pii-patterns/*');
    expect(policyJson).toContain('ToolsTable');
  });

  test('limits tenant provisioner AgentCore memory access to tagged tenant memories', () => {
    const statements = getIamPolicyStatements(template);
    const createMemory = statements.find((statement) => statement.Sid === 'TenantStackCreateTaggedMemory');
    const manageMemory = statements.find((statement) => statement.Sid === 'TenantStackManageTaggedMemory');
    const tagMemory = statements.find((statement) => statement.Sid === 'TenantStackTagManagedMemory');

    expect(createMemory).toMatchObject({
      Action: 'bedrock-agentcore:CreateMemory',
      Resource: '*',
      Condition: {
        StringEquals: {
          'aws:RequestTag/TenantManaged': 'true',
        },
      },
    });
    expect(manageMemory).toMatchObject({
      Action: ['bedrock-agentcore:UpdateMemory', 'bedrock-agentcore:DeleteMemory', 'bedrock-agentcore:GetMemory'],
      Resource: 'arn:aws:bedrock-agentcore:eu-west-2:123456789012:memory/*',
      Condition: {
        StringEquals: {
          'aws:ResourceTag/TenantManaged': 'true',
        },
      },
    });
    expect(tagMemory).toMatchObject({
      Action: 'bedrock-agentcore:TagResource',
      Resource: 'arn:aws:bedrock-agentcore:eu-west-2:123456789012:memory/*',
      Condition: {
        StringEquals: {
          'aws:RequestTag/TenantManaged': 'true',
          'aws:ResourceTag/TenantManaged': 'true',
        },
      },
    });
  });

  test('provisions SPA resources: S3 bucket, CloudFront distribution, and identifiers', () => {
    template.hasResourceProperties('AWS::S3::Bucket', {
      BucketEncryption: {
        ServerSideEncryptionConfiguration: [
          {
            ServerSideEncryptionByDefault: {
              SSEAlgorithm: 'AES256',
            },
          },
        ],
      },
      PublicAccessBlockConfiguration: {
        BlockPublicAcls: true,
        BlockPublicPolicy: true,
        IgnorePublicAcls: true,
        RestrictPublicBuckets: true,
      },
    });

    template.hasResourceProperties('AWS::CloudFront::Distribution', {
      DistributionConfig: Match.objectLike({
        Enabled: true,
        DefaultCacheBehavior: Match.objectLike({
          ViewerProtocolPolicy: 'redirect-to-https',
        }),
      }),
    });

    template.hasResourceProperties('AWS::SSM::Parameter', {
      Name: '/platform/spa/dev/bucket-name',
      Type: 'String',
    });

    template.hasResourceProperties('AWS::SSM::Parameter', {
      Name: '/platform/spa/dev/distribution-id',
      Type: 'String',
    });

    template.hasOutput('SpaBucketName', {
      Description: 'S3 bucket name for the platform SPA',
    });

    template.hasOutput('SpaDistributionId', {
      Description: 'CloudFront distribution ID for the platform SPA',
    });
  });

  test('configures CloudFront access logging for the SPA distribution with 30-day retention in dev', () => {
    template.hasResourceProperties('AWS::S3::Bucket', {
      BucketName: 'platform-spa-logs-dev',
      AccessControl: 'LogDeliveryWrite',
      LifecycleConfiguration: {
        Rules: [
          {
            ExpirationInDays: 30,
            Id: 'RetentionRule',
            Status: 'Enabled',
          },
        ],
      },
      OwnershipControls: {
        Rules: [
          {
            ObjectOwnership: 'BucketOwnerPreferred',
          },
        ],
      },
    });

    template.hasResourceProperties('AWS::CloudFront::Distribution', {
      DistributionConfig: Match.objectLike({
        Logging: Match.objectLike({
          Bucket: Match.anyValue(),
          IncludeCookies: false,
          Prefix: 'spa-cloudfront/',
        }),
      }),
    });
  });

  test('configures CloudFront access logging for the SPA distribution with 365-day retention in prod', () => {
    const prodTemplate = synthTemplate('prod');
    prodTemplate.hasResourceProperties('AWS::S3::Bucket', {
      BucketName: 'platform-spa-logs-prod',
      LifecycleConfiguration: {
        Rules: [
          {
            ExpirationInDays: 365,
            Id: 'RetentionRule',
            Status: 'Enabled',
          },
        ],
      },
    });
  });

  test('wires Entra config from CDK context instead of hardcoded common endpoints', () => {
    const customTemplate = synthTemplate('dev', {
      entraTenantId: '00000000-0000-0000-0000-000000000000',
      entraAudience: 'api://platform-dev',
    });

    const expectedJwksUrl =
      'https://login.microsoftonline.com/00000000-0000-0000-0000-000000000000/discovery/v2.0/keys';
    const expectedIssuer =
      'https://login.microsoftonline.com/00000000-0000-0000-0000-000000000000/v2.0';

    customTemplate.hasResourceProperties('AWS::Lambda::Function', {
      FunctionName: 'platform-core-dev-authoriser',
      Environment: {
        Variables: Match.objectLike({
          ENTRA_JWKS_URL: expectedJwksUrl,
          ENTRA_AUDIENCE: 'api://platform-dev',
          ENTRA_ISSUER: expectedIssuer,
        }),
      },
    });

    customTemplate.hasResourceProperties('AWS::Lambda::Function', {
      FunctionName: 'platform-core-dev-interceptor-request',
      Environment: {
        Variables: Match.objectLike({
          ENTRA_JWKS_URL: expectedJwksUrl,
          ENTRA_AUDIENCE: 'api://platform-dev',
          ENTRA_ISSUER: expectedIssuer,
        }),
      },
    });

    customTemplate.hasResourceProperties('AWS::Lambda::Function', {
      FunctionName: 'platform-core-dev-bff',
      Environment: {
        Variables: Match.objectLike({
          ENTRA_TENANT_ID: '00000000-0000-0000-0000-000000000000',
          ENTRA_AUDIENCE: 'api://platform-dev',
          ENTRA_TOKEN_ENDPOINT:
            'https://login.microsoftonline.com/00000000-0000-0000-0000-000000000000/oauth2/v2.0/token',
        }),
      },
    });
  });

  test('configures API Gateway access logs with numeric status and latency for metric filters', () => {
    template.hasResourceProperties('AWS::ApiGateway::Stage', {
      AccessLogSetting: {
        Format: Match.stringLikeRegexp('.*status":\\$context.status.*latency":\\$context.responseLatency.*'),
      },
    });

    template.hasResourceProperties('AWS::Logs::MetricFilter', {
      FilterPattern: '{ $.tenantId = "*" }',
      MetricTransformations: [
        Match.objectLike({
          MetricName: 'RequestCount',
          MetricNamespace: 'Platform/API',
          MetricValue: '1',
          Dimensions: Match.arrayWith([
            Match.objectLike({ Key: 'TenantId', Value: '$.tenantId' }),
          ]),
        }),
      ],
    });

    template.hasResourceProperties('AWS::Logs::MetricFilter', {
      FilterPattern: '{ $.status >= 400 }',
      MetricTransformations: [
        Match.objectLike({
          MetricName: 'ErrorCount',
          MetricNamespace: 'Platform/API',
          MetricValue: '1',
        }),
      ],
    });

    template.hasResourceProperties('AWS::Logs::MetricFilter', {
      FilterPattern: '{ $.latency = "*" }',
      MetricTransformations: [
        Match.objectLike({
          MetricName: 'Latency',
          MetricValue: '$.latency',
        }),
      ],
    });
  });

  test('grants bridge lambda namespace-scoped put-metric-data permissions', () => {
    expect(getScopedPutMetricStatements(template, 'bridgeLambdaServiceRole')).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          Action: 'cloudwatch:PutMetricData',
          Resource: '*',
          Condition: {
            StringEquals: {
              'cloudwatch:namespace': 'Platform/Bridge',
            },
          },
        }),
      ]),
    );
  });

  test('grants billing lambda namespace-scoped put-metric-data permissions', () => {
    expect(getScopedPutMetricStatements(template, 'billingLambdaServiceRole')).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          Action: 'cloudwatch:PutMetricData',
          Resource: '*',
          Condition: {
            StringEquals: {
              'cloudwatch:namespace': 'Platform/Billing',
            },
          },
        }),
      ]),
    );
  });

  test('does not synthesize unconditioned put-metric-data statements', () => {
    const putMetricStatements = getIamPolicyStatements(template).filter(
      (statement) => statement.Action === 'cloudwatch:PutMetricData',
    );

    expect(putMetricStatements.length).toBeGreaterThan(0);
    for (const statement of putMetricStatements) {
      expect(statement).toEqual(
        expect.objectContaining({
          Resource: '*',
          Condition: {
            StringEquals: {
              'cloudwatch:namespace': expect.stringMatching(/^Platform\/(Bridge|Billing)$/),
            },
          },
        }),
      );
    }
  });

  test('sets explicit TLS minimum protocol version on CloudFront even without custom domain', () => {
    template.hasResourceProperties('AWS::CloudFront::Distribution', {
      DistributionConfig: Match.objectLike({
        ViewerCertificate: Match.objectLike({
          CloudFrontDefaultCertificate: true,
          MinimumProtocolVersion: 'TLSv1.2_2021',
        }),
      }),
    });
  });

  test('configures CloudFront with custom domain, ACM certificate, and TLS policy when context provided', () => {
    const customDomainTemplate = synthTemplate('prod', {
      spaDomainName: 'app.example.com',
      spaCertificateArn: 'arn:aws:acm:us-east-1:123456789012:certificate/abcd-1234',
    });

    customDomainTemplate.hasResourceProperties('AWS::CloudFront::Distribution', {
      DistributionConfig: Match.objectLike({
        Aliases: ['app.example.com'],
        ViewerCertificate: Match.objectLike({
          AcmCertificateArn: 'arn:aws:acm:us-east-1:123456789012:certificate/abcd-1234',
          MinimumProtocolVersion: 'TLSv1.2_2021',
          SslSupportMethod: 'sni-only',
        }),
      }),
    });

    customDomainTemplate.hasResourceProperties('AWS::SSM::Parameter', {
      Name: '/platform/spa/prod/domain-name',
      Value: 'app.example.com',
    });

    customDomainTemplate.hasOutput('SpaDomainName', {
      Value: 'app.example.com',
    });
  });

  test('rejects incomplete SPA custom domain context before synthesizing CloudFront', () => {
    expect(() =>
      synthTemplate('prod', {
        spaDomainName: 'app.example.com',
      }),
    ).toThrow('Custom SPA domain configuration requires both spaDomainName and spaCertificateArn');
  });

  test('rejects SPA certificate ARNs outside CloudFronts required region', () => {
    expect(() =>
      synthTemplate('prod', {
        spaDomainName: 'app.example.com',
        spaCertificateArn: 'arn:aws:acm:eu-west-2:123456789012:certificate/abcd-1234',
      }),
    ).toThrow('spaCertificateArn must reference an ACM certificate in us-east-1 for CloudFront');
  });

  test('uses explicit custom-domain origins in prod connect-src and excludes localhost allowances', () => {
    const customDomainTemplate = synthTemplate('prod', {
      spaDomainName: 'app.example.com',
      spaCertificateArn: 'arn:aws:acm:us-east-1:123456789012:certificate/abcd-1234',
      apiDomainName: 'api.example.com',
      apiCertificateArn: 'arn:aws:acm:eu-west-2:123456789012:certificate/efgh-5678',
      agUiEndpointOrigins: 'https://ag-ui.example.com/connect',
    });

    const contentSecurityPolicy = JSON.stringify(getSpaContentSecurityPolicy(customDomainTemplate));

    expect(contentSecurityPolicy).toContain('https://app.example.com');
    expect(contentSecurityPolicy).toContain('https://api.example.com');
    expect(contentSecurityPolicy).toContain('https://ag-ui.example.com');
    expect(contentSecurityPolicy).toContain('https://login.microsoftonline.com');
    expect(contentSecurityPolicy).not.toContain('localhost:3000');
    expect(contentSecurityPolicy).not.toContain('localhost:4566');
    expect(contentSecurityPolicy).not.toContain('localhost:8080');
    expect(contentSecurityPolicy).not.toMatch(/connect-src\s+'self'\s+https:\s*;/);
    expect(String(getSpaContentSecurityPolicy(customDomainTemplate)).length).toBeLessThan(1784);
  });

  test('uses CloudFront generated domain for CORS when no custom domain is set', () => {
    const distributions = template.findResources('AWS::CloudFront::Distribution');
    expect(Object.keys(distributions)).toHaveLength(1);

    const optionsMethods = template.findResources('AWS::ApiGateway::Method', {
      Properties: {
        HttpMethod: 'OPTIONS',
      },
    });
    for (const method of Object.values(optionsMethods) as Array<{ Properties?: unknown }>) {
      const properties = method.Properties as {
        Integration?: { IntegrationResponses?: Array<{ ResponseParameters?: Record<string, unknown> }> };
      };
      const responseParameters =
        properties.Integration?.IntegrationResponses?.[0]?.ResponseParameters ?? {};
      const allowOrigin = responseParameters['method.response.header.Access-Control-Allow-Origin'];
      expect(JSON.stringify(allowOrigin)).toContain('DomainName');
    }
  });

  test('uses custom domain for CORS origin when spaDomainName is set', () => {
    const customDomainTemplate = synthTemplate('prod', {
      spaDomainName: 'app.example.com',
      spaCertificateArn: 'arn:aws:acm:us-east-1:123456789012:certificate/abcd-1234',
    });

    const gatewayResponses = customDomainTemplate.findResources('AWS::ApiGateway::GatewayResponse');
    for (const response of Object.values(gatewayResponses) as Array<{ Properties?: Record<string, unknown> }>) {
      const headers = response.Properties?.ResponseParameters as Record<string, string> | undefined;
      if (headers) {
        const originHeader = headers['gatewayresponse.header.Access-Control-Allow-Origin'];
        if (originHeader) {
          expect(originHeader).toBe("'https://app.example.com'");
        }
      }
    }
  });

  test('configures API Gateway custom domain with TLS 1.2 when context provided', () => {
    const customDomainTemplate = synthTemplate('prod', {
      apiDomainName: 'api.example.com',
      apiCertificateArn: 'arn:aws:acm:eu-west-2:123456789012:certificate/efgh-5678',
    });

    customDomainTemplate.hasResourceProperties('AWS::ApiGateway::DomainName', {
      DomainName: 'api.example.com',
      EndpointConfiguration: {
        Types: ['REGIONAL'],
      },
      SecurityPolicy: 'TLS_1_2',
    });

    customDomainTemplate.resourceCountIs('AWS::ApiGateway::BasePathMapping', 1);

    customDomainTemplate.hasResourceProperties('AWS::SSM::Parameter', {
      Name: '/platform/core/prod/api-domain-name',
      Value: 'api.example.com',
    });

    customDomainTemplate.hasOutput('ApiCustomDomainName', {
      Value: 'api.example.com',
    });
  });

  test('does not create API Gateway custom domain when context is absent', () => {
    template.resourceCountIs('AWS::ApiGateway::DomainName', 0);
    template.resourceCountIs('AWS::ApiGateway::BasePathMapping', 0);
  });

  test('provisions Valkey cluster (ElastiCache Serverless) for TPM rate limiting', () => {
    template.hasResourceProperties('AWS::ElastiCache::ServerlessCache', {
      Engine: 'valkey',
      ServerlessCacheName: 'platform-valkey-dev',
      SubnetIds: Match.anyValue(),
      SecurityGroupIds: Match.anyValue(),
    });

    template.hasResourceProperties('AWS::EC2::SecurityGroup', {
      GroupDescription: 'Security group for platform Valkey cluster (ElastiCache Serverless)',
    });

    template.hasResourceProperties('AWS::EC2::SecurityGroup', {
      GroupDescription: 'Bridge Lambda client access to platform Valkey',
    });

    template.hasResourceProperties('AWS::EC2::SecurityGroupIngress', {
      FromPort: 6379,
      ToPort: 6379,
      IpProtocol: 'tcp',
      SourceSecurityGroupId: {
        'Fn::GetAtt': Match.arrayWith([Match.stringLikeRegexp('BridgeValkeyClientSecurityGroup')]),
      },
      GroupId: {
        'Fn::GetAtt': Match.arrayWith([Match.stringLikeRegexp('ValkeySecurityGroup')]),
      },
    });

    template.hasResourceProperties('AWS::EC2::SecurityGroupEgress', {
      FromPort: 6379,
      ToPort: 6379,
      IpProtocol: 'tcp',
      DestinationSecurityGroupId: {
        'Fn::GetAtt': Match.arrayWith([Match.stringLikeRegexp('ValkeySecurityGroup')]),
      },
      GroupId: {
        'Fn::GetAtt': Match.arrayWith([Match.stringLikeRegexp('BridgeValkeyClientSecurityGroup')]),
      },
    });

    template.hasResourceProperties('AWS::SSM::Parameter', {
      Name: '/platform/dev/config/valkey-endpoint',
      Type: 'String',
    });

    const stagingTemplate = synthTemplate('staging');
    stagingTemplate.hasResourceProperties('AWS::SSM::Parameter', {
      Name: '/platform/staging/config/valkey-endpoint',
      Type: 'String',
    });
  });
});
