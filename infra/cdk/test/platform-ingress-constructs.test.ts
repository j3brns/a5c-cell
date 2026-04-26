import * as cdk from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import { PlatformApi } from '../lib/platform-api';
import { PlatformGateway } from '../lib/platform-gateway';
import { PlatformSpa } from '../lib/platform-spa';
import { PlatformWaf } from '../lib/platform-waf';

const TEST_ENV = {
  account: '123456789012',
  region: 'eu-west-2',
};

function createNodejsFunction(scope: cdk.Stack, id: string): lambda.Function {
  return new lambda.Function(scope, id, {
    runtime: lambda.Runtime.NODEJS_20_X,
    handler: 'index.handler',
    code: lambda.Code.fromInline(
      'exports.handler = async () => ({ statusCode: 200, body: "ok" });',
    ),
  });
}

function synthSpa(
  extraProps: Partial<{
    envName: string;
    spaDomainName: string;
    spaCertificateArn: string;
    spaWebAclArn: string;
    apiAllowedOrigin: string;
    entraAuthorityOrigin: string;
    agUiAllowedOrigins: string[];
  }> = {},
) {
  const app = new cdk.App();
  const stack = new cdk.Stack(app, 'PlatformSpaTestStack', { env: TEST_ENV });
  new PlatformSpa(stack, 'PlatformSpa', {
    envName: 'dev',
    apiAllowedOrigin: 'https://api.example.com',
    entraAuthorityOrigin: 'https://login.microsoftonline.com',
    ...extraProps,
  });
  return Template.fromStack(stack);
}

function synthSpaWithoutEnv(extraProps: Partial<{ spaDomainName: string; spaCertificateArn: string }> = {}) {
  const app = new cdk.App();
  const stack = new cdk.Stack(app, 'PlatformSpaEnvAgnosticTestStack');
  new PlatformSpa(stack, 'PlatformSpa', {
    envName: 'dev',
    apiAllowedOrigin: 'https://api.example.com',
    entraAuthorityOrigin: 'https://login.microsoftonline.com',
    ...extraProps,
  });
  return Template.fromStack(stack);
}

function getSpaContentSecurityPolicy(template: Template): string {
  const policies = template.findResources('AWS::CloudFront::ResponseHeadersPolicy') as Record<
    string,
    {
      Properties?: {
        ResponseHeadersPolicyConfig?: {
          SecurityHeadersConfig?: {
            ContentSecurityPolicy?: {
              ContentSecurityPolicy?: string;
            };
          };
        };
      };
    }
  >;

  const [policy] = Object.values(policies);
  const contentSecurityPolicy =
    policy?.Properties?.ResponseHeadersPolicyConfig?.SecurityHeadersConfig?.ContentSecurityPolicy
      ?.ContentSecurityPolicy;

  expect(contentSecurityPolicy).toBeDefined();
  return contentSecurityPolicy!;
}

function synthApi(
  extraProps: Partial<{ apiDomainName: string; apiCertificateArn: string }> = {},
) {
  const app = new cdk.App();
  const stack = new cdk.Stack(app, 'PlatformApiTestStack', { env: TEST_ENV });

  const authoriserFn = createNodejsFunction(stack, 'AuthoriserFn');
  const bridgeFn = createNodejsFunction(stack, 'BridgeFn');

  const authoriserAlias = new lambda.Alias(stack, 'AuthoriserAlias', {
    aliasName: 'live',
    version: authoriserFn.currentVersion,
  });
  const bridgeAlias = new lambda.Alias(stack, 'BridgeAlias', {
    aliasName: 'live',
    version: bridgeFn.currentVersion,
  });

  new PlatformApi(stack, 'PlatformApi', {
    envName: 'dev',
    spaAllowedOrigin: 'https://spa.example.com',
    authoriserAlias,
    tenantMgmtFn: createNodejsFunction(stack, 'TenantMgmtFn'),
    webhookRegistryFn: createNodejsFunction(stack, 'WebhookRegistryFn'),
    agentRegistryFn: createNodejsFunction(stack, 'AgentRegistryFn'),
    adminOpsFn: createNodejsFunction(stack, 'AdminOpsFn'),
    bridgeAlias,
    bffFn: createNodejsFunction(stack, 'BffFn'),
    ...extraProps,
  });

  return Template.fromStack(stack);
}

function synthGateway(enforcementMode: 'LOG_ONLY' | 'ENFORCE' = 'LOG_ONLY') {
  const app = new cdk.App();
  const stack = new cdk.Stack(app, 'PlatformGatewayTestStack', { env: TEST_ENV });

  new PlatformGateway(stack, 'PlatformGateway', {
    enforcementMode,
    policyEngineName: `PlatformGatewayPolicyEngine${enforcementMode}`,
    policyName: `PlatformGatewayTenantRoleAccess${enforcementMode}`,
    requestInterceptorFn: createNodejsFunction(stack, 'RequestInterceptorFn'),
    responseInterceptorFn: createNodejsFunction(stack, 'ResponseInterceptorFn'),
    diagnosticsToolFn: createNodejsFunction(stack, 'DiagnosticsToolFn'),
    toolsTable: dynamodb.Table.fromTableArn(
      stack,
      'ToolsTable',
      'arn:aws:dynamodb:eu-west-2:123456789012:table/platform-tools',
    ),
  });

  return Template.fromStack(stack);
}

function synthWaf() {
  const app = new cdk.App();
  const stack = new cdk.Stack(app, 'PlatformWafTestStack', { env: TEST_ENV });
  const api = new apigateway.RestApi(stack, 'TestApi');
  api.root.addMethod(
    'GET',
    new apigateway.MockIntegration({
      integrationResponses: [
        {
          statusCode: '200',
        },
      ],
      passthroughBehavior: apigateway.PassthroughBehavior.NEVER,
      requestTemplates: {
        'application/json': '{"statusCode": 200}',
      },
    }),
    {
      methodResponses: [
        {
          statusCode: '200',
        },
      ],
    },
  );

  new PlatformWaf(stack, 'PlatformWaf', {
    api,
  });

  return Template.fromStack(stack);
}

function getWebAclRules(template: Template): Array<{
  Name: string;
  Priority: number;
  VisibilityConfig?: {
    MetricName?: string;
    SampledRequestsEnabled?: boolean;
  };
}> {
  const webAcls = template.findResources('AWS::WAFv2::WebACL') as Record<
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
}

describe('PlatformSpa', () => {
  test('keeps OAC, CSP headers, SPA rewrite, and cache separation intact', () => {
    const template = synthSpa();

    template.resourceCountIs('AWS::CloudFront::OriginAccessControl', 1);
    template.resourceCountIs('AWS::CloudFront::Function', 1);

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
        DefaultCacheBehavior: Match.objectLike({
          CachePolicyId: cloudfront.CachePolicy.CACHING_DISABLED.cachePolicyId,
          FunctionAssociations: Match.arrayWith([
            Match.objectLike({
              EventType: 'viewer-request',
              FunctionARN: Match.anyValue(),
            }),
          ]),
        }),
        CacheBehaviors: Match.arrayWith([
          Match.objectLike({
            PathPattern: 'assets/*',
            CachePolicyId: cloudfront.CachePolicy.CACHING_OPTIMIZED.cachePolicyId,
          }),
        ]),
      }),
    });

    const distributions = template.findResources('AWS::CloudFront::Distribution');
    const [distribution] = Object.values(distributions) as Array<{
      Properties?: { DistributionConfig?: Record<string, unknown> };
    }>;

    expect(distribution.Properties?.DistributionConfig).not.toHaveProperty('CustomErrorResponses');
  });

  test('attaches the provided CloudFront web ACL ARN to the SPA distribution', () => {
    const template = synthSpa({
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

  test('uses explicit connect-src origins and omits the https wildcard', () => {
    const template = synthSpa({
      spaDomainName: 'spa.example.com',
      spaCertificateArn:
        'arn:aws:acm:us-east-1:123456789012:certificate/11111111-1111-1111-1111-111111111111',
      apiAllowedOrigin: 'https://api.example.com',
      entraAuthorityOrigin: 'https://login.microsoftonline.com',
      agUiAllowedOrigins: ['https://ag-ui.example.com'],
    });

    const contentSecurityPolicy = getSpaContentSecurityPolicy(template);

    expect(contentSecurityPolicy).toContain("connect-src 'self'");
    expect(contentSecurityPolicy).toContain('https://spa.example.com');
    expect(contentSecurityPolicy).toContain('https://api.example.com');
    expect(contentSecurityPolicy).toContain('https://login.microsoftonline.com');
    expect(contentSecurityPolicy).toContain('https://ag-ui.example.com');
    expect(contentSecurityPolicy).toContain('http://localhost:3000');
    expect(contentSecurityPolicy).toContain('http://localhost:4566');
    expect(contentSecurityPolicy).toContain('http://localhost:8080');
    expect(contentSecurityPolicy).not.toMatch(/connect-src\s+'self'\s+https:\s*;/);
  });

  test('limits localhost connect-src allowances to non-production environments', () => {
    const productionTemplate = synthSpa({
      envName: 'prod',
      spaDomainName: 'spa.example.com',
      spaCertificateArn:
        'arn:aws:acm:us-east-1:123456789012:certificate/11111111-1111-1111-1111-111111111111',
    });

    const contentSecurityPolicy = getSpaContentSecurityPolicy(productionTemplate);

    expect(contentSecurityPolicy).not.toContain('http://localhost:3000');
    expect(contentSecurityPolicy).not.toContain('http://localhost:4566');
    expect(contentSecurityPolicy).not.toContain('http://localhost:8080');
  });

  test('fails synth when the serialized CSP would exceed the CloudFront limit', () => {
    expect(() =>
      synthSpa({
        spaDomainName: 'spa.example.com',
        spaCertificateArn:
          'arn:aws:acm:us-east-1:123456789012:certificate/11111111-1111-1111-1111-111111111111',
        agUiAllowedOrigins: Array.from({ length: 100 }, (_, index) => `https://ag-ui-${index}.example.com`),
      }),
    ).toThrow(
      'SPA Content-Security-Policy exceeds the CloudFront response headers policy limit of 1783 characters',
    );
  });

  test('preserves custom-domain TLS posture when domain inputs are provided', () => {
    const template = synthSpa({
      spaDomainName: 'spa.example.com',
      spaCertificateArn:
        'arn:aws:acm:us-east-1:123456789012:certificate/11111111-1111-1111-1111-111111111111',
    });

    template.hasResourceProperties('AWS::CloudFront::Distribution', {
      DistributionConfig: Match.objectLike({
        Aliases: ['spa.example.com'],
        ViewerCertificate: Match.objectLike({
          AcmCertificateArn:
            'arn:aws:acm:us-east-1:123456789012:certificate/11111111-1111-1111-1111-111111111111',
          MinimumProtocolVersion: 'TLSv1.2_2021',
          SslSupportMethod: 'sni-only',
        }),
      }),
    });
  });

  test('rejects a custom SPA domain without a certificate ARN', () => {
    expect(() =>
      synthSpa({
        spaDomainName: 'spa.example.com',
      }),
    ).toThrow('Custom SPA domain configuration requires both spaDomainName and spaCertificateArn');
  });

  test('rejects a certificate ARN without a custom SPA domain', () => {
    expect(() =>
      synthSpa({
        spaCertificateArn:
          'arn:aws:acm:us-east-1:123456789012:certificate/11111111-1111-1111-1111-111111111111',
      }),
    ).toThrow('Custom SPA domain configuration requires both spaDomainName and spaCertificateArn');
  });

  test('rejects non-ACM certificate ARNs for the SPA custom domain', () => {
    expect(() =>
      synthSpa({
        spaDomainName: 'spa.example.com',
        spaCertificateArn: 'arn:aws:iam::123456789012:server-certificate/example',
      }),
    ).toThrow('spaCertificateArn must be an ACM certificate ARN');
  });

  test('rejects ACM certificate ARNs outside us-east-1 for the SPA custom domain', () => {
    expect(() =>
      synthSpa({
        spaDomainName: 'spa.example.com',
        spaCertificateArn:
          'arn:aws:acm:eu-west-2:123456789012:certificate/11111111-1111-1111-1111-111111111111',
      }),
    ).toThrow('spaCertificateArn must reference an ACM certificate in us-east-1 for CloudFront');
  });

  test('rejects cross-account ACM certificate ARNs for the SPA custom domain', () => {
    expect(() =>
      synthSpa({
        spaDomainName: 'spa.example.com',
        spaCertificateArn:
          'arn:aws:acm:us-east-1:210987654321:certificate/11111111-1111-1111-1111-111111111111',
      }),
    ).toThrow('must match the stack account 123456789012');
  });

  test('rejects blank SPA custom-domain inputs', () => {
    expect(() =>
      synthSpa({
        spaDomainName: '   ',
        spaCertificateArn: '   ',
      }),
    ).toThrow('spaDomainName and spaCertificateArn must not be blank when provided');
  });

  test('rejects empty-string SPA custom-domain inputs', () => {
    expect(() =>
      synthSpa({
        spaDomainName: '',
        spaCertificateArn: '',
      }),
    ).toThrow('spaDomainName and spaCertificateArn must not be blank when provided');
  });

  test('rejects SPA certificate ARNs when the stack account is unresolved', () => {
    expect(() =>
      synthSpaWithoutEnv({
        spaDomainName: 'spa.example.com',
        spaCertificateArn:
          'arn:aws:acm:us-east-1:210987654321:certificate/11111111-1111-1111-1111-111111111111',
      }),
    ).toThrow('spaCertificateArn requires a concrete stack account');
  });
});

describe('PlatformApi', () => {
  test('keeps authorizer-backed usage plans, canonical routes, and CORS responses', () => {
    const template = synthApi();

    template.hasResourceProperties('AWS::ApiGateway::RestApi', {
      ApiKeySourceType: 'AUTHORIZER', // pragma: allowlist secret
    });
    template.resourceCountIs('AWS::ApiGateway::UsagePlan', 3);

    template.hasResourceProperties('AWS::ApiGateway::GatewayResponse', {
      ResponseType: 'DEFAULT_4XX',
      ResponseParameters: Match.objectLike({
        'gatewayresponse.header.gatewayresponses.header.Access-Control-Allow-Origin':
          "'https://spa.example.com'",
      }),
    });

    const resources = template.findResources('AWS::ApiGateway::Resource');
    const pathParts = Object.values(resources).map((resource) => {
      const properties = (resource as { Properties?: { PathPart?: string } }).Properties;
      return properties?.PathPart;
    });

    expect(pathParts).toEqual(
      expect.arrayContaining(['agents', '{agentName}', 'invoke', 'jobs', '{jobId}', 'webhooks']),
    );
  });

  test('adds the documented TLS 1.2 regional custom domain when configured', () => {
    const template = synthApi({
      apiDomainName: 'api.example.com',
      apiCertificateArn:
        'arn:aws:acm:eu-west-2:123456789012:certificate/22222222-2222-2222-2222-222222222222',
    });

    template.hasResourceProperties('AWS::ApiGateway::DomainName', {
      DomainName: 'api.example.com',
      EndpointConfiguration: {
        Types: ['REGIONAL'],
      },
      SecurityPolicy: 'TLS_1_2',
      RegionalCertificateArn:
        'arn:aws:acm:eu-west-2:123456789012:certificate/22222222-2222-2222-2222-222222222222',
    });
  });
});

describe('PlatformWaf', () => {
  test('keeps the API WebACL rules and association intact', () => {
    const template = synthWaf();
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
});

describe('PlatformGateway', () => {
  function gatewayCedarStatement(template: Template): string {
    const policies = template.findResources('AWS::BedrockAgentCore::Policy');
    const policy = Object.values(policies)[0] as {
      Properties?: { Definition?: { Cedar?: { Statement?: unknown } } };
    };
    const statement = policy.Properties?.Definition?.Cedar?.Statement;
    if (typeof statement === 'string') {
      return statement;
    }
    const fnSub = (statement as { 'Fn::Sub'?: string | [string, unknown] } | undefined)?.['Fn::Sub'];
    if (Array.isArray(fnSub)) {
      return fnSub[0];
    }
    return String(fnSub ?? '');
  }

  test('keeps MCP gateway, Cedar policy, and non-wildcard policy-engine access', () => {
    const template = synthGateway();

    template.hasResourceProperties('AWS::BedrockAgentCore::Gateway', {
      AuthorizerType: 'AWS_IAM',
      ProtocolType: 'MCP',
      PolicyEngineConfiguration: Match.objectLike({
        Mode: 'LOG_ONLY',
      }),
      InterceptorConfigurations: Match.arrayWith([
        Match.objectLike({
          InterceptionPoints: ['REQUEST'],
          InputConfiguration: {
            PassRequestHeaders: true,
          },
        }),
        Match.objectLike({
          InterceptionPoints: ['RESPONSE'],
          InputConfiguration: {
            PassRequestHeaders: true,
          },
        }),
      ]),
    });

    template.hasResourceProperties('AWS::BedrockAgentCore::PolicyEngine', {
      Name: 'PlatformGatewayPolicyEngineLOG_ONLY',
    });
    template.hasResourceProperties('AWS::BedrockAgentCore::Policy', {
      Name: 'PlatformGatewayTenantRoleAccessLOG_ONLY',
      ValidationMode: 'FAIL_ON_ANY_FINDINGS',
    });

    const policies = template.findResources('AWS::IAM::Policy');
    const allStatements = Object.values(policies).flatMap((resource) => {
      const properties = (
        resource as {
          Properties?: { PolicyDocument?: { Statement?: Array<Record<string, unknown>> } };
        }
      ).Properties;
      return properties?.PolicyDocument?.Statement ?? [];
    });

    const gatewayPolicyStatement = allStatements.find((statement) => {
      const actions = Array.isArray(statement.Action) ? statement.Action : [statement.Action];
      return actions.includes('bedrock-agentcore:GetPolicyEngine');
    });

    expect(gatewayPolicyStatement).toBeDefined();
    expect(gatewayPolicyStatement?.Resource).not.toBe('*');
  });

  test('uses deploy-safe Cedar scoped to tenant execution roles and the concrete gateway', () => {
    const template = synthGateway();
    const statement = gatewayCedarStatement(template);

    expect(statement).toContain('principal is AgentCore::IamEntity');
    expect(statement).toContain('principal.id like');
    expect(statement).toContain(':assumed-role/platform-tenant-*-execution-role/');
    expect(statement).toContain('resource == AgentCore::Gateway::');
    expect(statement).toContain('action,');
    expect(statement).not.toContain('when {\n  true\n}');
    expect(statement).not.toContain('principal,\n  action,\n  resource');
  });

  test('switches the policy engine mode to ENFORCE for prod posture', () => {
    const template = synthGateway('ENFORCE');

    template.hasResourceProperties('AWS::BedrockAgentCore::Gateway', {
      PolicyEngineConfiguration: Match.objectLike({
        Mode: 'ENFORCE',
      }),
    });
  });

  test('registers the platform diagnostics Lambda as an MCP Gateway target', () => {
    const template = synthGateway();

    template.hasResourceProperties('AWS::BedrockAgentCore::GatewayTarget', {
      Name: 'platform-diagnostics',
      GatewayIdentifier: Match.anyValue(),
      MetadataConfiguration: {
        AllowedRequestHeaders: ['x-tenant-id', 'x-app-id'],
      },
      CredentialProviderConfigurations: [
        {
          CredentialProviderType: 'GATEWAY_IAM_ROLE',
          CredentialProvider: {
            IamCredentialProvider: {
              Service: 'lambda',
              Region: 'eu-west-2',
            },
          },
        },
      ],
      TargetConfiguration: {
        Mcp: {
          Lambda: {
            LambdaArn: Match.anyValue(),
            ToolSchema: {
              InlinePayload: Match.arrayWith([
                Match.objectLike({
                  Name: 'get_platform_health',
                  InputSchema: { Type: 'object', Properties: {}, Required: [] },
                }),
                Match.objectLike({
                  Name: 'get_tenant_status',
                  InputSchema: Match.objectLike({
                    Type: 'object',
                    Required: ['tenant_id'],
                  }),
                }),
                Match.objectLike({ Name: 'get_recent_errors' }),
                Match.objectLike({ Name: 'get_runbook_guidance' }),
              ]),
            },
          },
        },
      },
    });

    const policyJson = JSON.stringify(template.findResources('AWS::IAM::Policy'));
    expect(policyJson).toContain('DiagnosticsToolFn');
    expect(policyJson).toContain('lambda:InvokeFunction');

    const customResources = template.findResources('Custom::AWS');
    const customResourceJson = JSON.stringify(customResources);
    expect(Object.keys(customResources)).toHaveLength(4);
    expect(customResourceJson).toContain('TOOL#get_platform_health');
    expect(customResourceJson).toContain('TOOL#get_tenant_status');
    expect(customResourceJson).toContain('TOOL#get_recent_errors');
    expect(customResourceJson).toContain('TOOL#get_runbook_guidance');
    expect(customResourceJson).toContain('TENANT#platform');
  });
});
