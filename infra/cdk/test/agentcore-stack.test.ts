import * as cdk from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { AgentCoreStack } from '../lib/agentcore-stack';
import {
  DEFAULT_AGENTCORE_TENANT_MEMORY_TEMPLATE,
  TENANT_MEMORY_TEMPLATE_PARAMETER_NAME,
} from '../lib/agentcore-memory-template';

describe('AgentCoreStack (TASK-024)', () => {
  const synthTemplate = () => {
    const app = new cdk.App({
      context: {
        env: 'dev',
        entraTenantId: '00000000-0000-0000-0000-000000000000',
        entraAudience: 'api://platform-dev',
      },
    });

    const stack = new AgentCoreStack(app, 'platform-agentcore-dev', {
      env: { region: 'eu-west-2' },
      homeRegion: 'eu-west-2',
      runtimeNetworkMode: 'VPC',
      runtimeSubnetIds: ['subnet-11111111', 'subnet-22222222'],
      runtimeSecurityGroupIds: ['sg-11111111'],
    });

    return Template.fromStack(stack);
  };

  const template = synthTemplate();

  test('creates runtime and endpoint with Entra JWT authorizer wiring', () => {
    template.resourceCountIs('AWS::BedrockAgentCore::Runtime', 1);
    template.resourceCountIs('AWS::BedrockAgentCore::RuntimeEndpoint', 1);

    template.hasResourceProperties('AWS::BedrockAgentCore::Runtime', {
      AgentRuntimeName: 'PlatformdevRuntime',
      NetworkConfiguration: {
        NetworkMode: 'VPC',
        NetworkModeConfig: {
          VpcConfig: {
            Subnets: ['subnet-11111111', 'subnet-22222222'],
            SecurityGroups: ['sg-11111111'],
          },
        },
      },
      ProtocolConfiguration: 'HTTP',
      AuthorizerConfiguration: {
        CustomJWTAuthorizer: {
          DiscoveryUrl:
            'https://login.microsoftonline.com/00000000-0000-0000-0000-000000000000/v2.0/.well-known/openid-configuration',
          AllowedAudience: ['api://platform-dev'],
        },
      },
      RequestHeaderConfiguration: {
        RequestHeaderAllowlist: ['authorization', 'x-tenant-id', 'x-app-id'],
      },
    });
  });

  test('records the VPC runtime posture as an explicit, reviewable decision', () => {
    template.hasResource('AWS::BedrockAgentCore::Runtime', {
      Metadata: {
        RuntimeNetworkPosture: {
          Decision: 'ADR-023_VPC',
          Justification: 'ADR-023_SECURE_RUNTIME_BASELINE',
        },
      },
      Properties: {
        NetworkConfiguration: {
          NetworkMode: 'VPC',
        },
        Tags: Match.objectLike({
          networkMode: 'VPC',
          networkPosture: 'ADR-023_VPC',
        }),
      },
    });
  });

  test('creates SSM parameters for memory template and Entra JWKS URL', () => {
    template.hasResourceProperties('AWS::SSM::Parameter', {
      Name: TENANT_MEMORY_TEMPLATE_PARAMETER_NAME,
      Type: 'String',
      Value: Match.serializedJson(
        Match.objectLike({
          provisionedBy: DEFAULT_AGENTCORE_TENANT_MEMORY_TEMPLATE.provisionedBy,
          eventExpiryDurationDays: DEFAULT_AGENTCORE_TENANT_MEMORY_TEMPLATE.eventExpiryDurationDays,
          semanticMemory: Match.objectLike({
            strategy: DEFAULT_AGENTCORE_TENANT_MEMORY_TEMPLATE.semanticMemory.strategy,
            namespaceTemplate:
              DEFAULT_AGENTCORE_TENANT_MEMORY_TEMPLATE.semanticMemory.namespaceTemplate,
          }),
        }),
      ),
    });

    template.hasResourceProperties('AWS::SSM::Parameter', {
      Name: '/platform/auth/jwks-url',
      Type: 'String',
      Value: 'https://login.microsoftonline.com/00000000-0000-0000-0000-000000000000/discovery/v2.0/keys',
    });
  });

  test('does not create a cross-region AgentCore metric stream', () => {
    template.resourceCountIs('AWS::CloudWatch::MetricStream', 0);
  });

  test('exports runtime region and memory template parameter name', () => {
    template.hasOutput('AgentCoreRuntimeRegion', {
      Value: 'eu-west-2',
    });
    template.hasOutput('AgentCoreRuntimeNetworkMode', {
      Value: 'VPC',
    });
    template.hasOutput('AgentCoreRuntimeNetworkPostureDecision', {
      Value: 'ADR-023_VPC',
    });

    template.hasOutput('TenantMemoryTemplateParameterName', {
      Value: Match.anyValue(),
    });
  });
});
