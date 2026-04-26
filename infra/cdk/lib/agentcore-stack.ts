/**
 * AgentCoreStack — AgentCore Runtime configuration and cross-region wiring.
 *
 * Runtime configuration: eu-west-2 (London) — see ADR-023.
 * Memory template: provisioned per-tenant in TenantStack.
 * Identity configuration for Entra JWKS.
 *
 * Implemented in TASK-024.
 * ADRs: ADR-001, ADR-023
 */
import * as cdk from 'aws-cdk-lib';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import { Construct } from 'constructs';
import { resolveEntraConfiguration } from './entra-config';
import {
  serializeAgentCoreTenantMemoryTemplate,
  TENANT_MEMORY_TEMPLATE_PARAMETER_NAME,
} from './agentcore-memory-template';
import { HOME_REGION, RUNTIME_NETWORK_MODE } from './runtime-topology';

export interface AgentCoreStackProps extends cdk.StackProps {
  readonly homeRegion: string;
  readonly runtimeNetworkMode: typeof RUNTIME_NETWORK_MODE;
  readonly runtimeSubnetIds: readonly string[];
  readonly runtimeSecurityGroupIds: readonly string[];
}

export class AgentCoreStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: AgentCoreStackProps) {
    super(scope, id, props);

    const envName = this.requiredContext('env');
    const entra = resolveEntraConfiguration(this);
    const runtimeRegion = cdk.Stack.of(this).region;

    if (!cdk.Token.isUnresolved(runtimeRegion) && runtimeRegion !== HOME_REGION) {
      throw new Error(`AgentCoreStack must be deployed in ${HOME_REGION}`);
    }
    if (props.homeRegion !== HOME_REGION) {
      throw new Error(`AgentCoreStack homeRegion must be ${HOME_REGION}`);
    }
    if (props.runtimeNetworkMode !== RUNTIME_NETWORK_MODE) {
      throw new Error('AgentCoreStack requires the ADR-023 VPC runtime network mode');
    }
    if (props.runtimeSubnetIds.length === 0 || props.runtimeSecurityGroupIds.length === 0) {
      throw new Error('AgentCoreStack VPC mode requires runtime subnets and security groups');
    }

    const runtimeExecutionRoleArn = new cdk.CfnParameter(this, 'RuntimeExecutionRoleArn', {
      type: 'String',
      description: 'IAM role ARN used by AWS::BedrockAgentCore::Runtime',
    });
    const runtimeArtifactBucketName = new cdk.CfnParameter(this, 'RuntimeArtifactBucketName', {
      type: 'String',
      description: 'S3 bucket containing zipped AgentCore runtime artifacts',
    });
    const runtimeArtifactPrefix = new cdk.CfnParameter(this, 'RuntimeArtifactPrefix', {
      type: 'String',
      description: 'S3 key prefix for the runtime artifact object',
    });
    const runtimeName = this.runtimeName(envName);
    const runtimeEndpointName = this.runtimeEndpointName(envName);

    const runtime = new cdk.CfnResource(this, 'AgentCoreRuntime', {
      type: 'AWS::BedrockAgentCore::Runtime',
      properties: {
        AgentRuntimeName: runtimeName,
        Description: `Primary AgentCore runtime for ${envName} (${runtimeRegion})`,
        RoleArn: runtimeExecutionRoleArn.valueAsString,
        AgentRuntimeArtifact: {
          CodeConfiguration: {
            Runtime: 'PYTHON_3_12',
            EntryPoint: ['handler.py'],
            Code: {
              S3: {
                Bucket: runtimeArtifactBucketName.valueAsString,
                Prefix: runtimeArtifactPrefix.valueAsString,
              },
            },
          },
        },
        NetworkConfiguration: {
          NetworkMode: props.runtimeNetworkMode,
          NetworkModeConfig: {
            VpcConfig: {
              Subnets: [...props.runtimeSubnetIds],
              SecurityGroups: [...props.runtimeSecurityGroupIds],
            },
          },
        },
        ProtocolConfiguration: 'HTTP',
        AuthorizerConfiguration: {
          CustomJWTAuthorizer: {
            DiscoveryUrl: entra.discoveryUrl,
            AllowedAudience: [entra.audience],
          },
        },
        RequestHeaderConfiguration: {
          RequestHeaderAllowlist: ['authorization', 'x-tenant-id', 'x-app-id'],
        },
        EnvironmentVariables: {
          HOME_REGION: props.homeRegion,
          RUNTIME_REGION: runtimeRegion,
        },
        Tags: {
          component: 'agentcore-runtime',
          environment: envName,
          homeRegion: props.homeRegion,
          networkMode: props.runtimeNetworkMode,
          networkPosture: 'ADR-023_VPC',
        },
      },
    });
    runtime.cfnOptions.metadata = {
      RuntimeNetworkPosture: {
        Decision: 'ADR-023_VPC',
        Justification: 'ADR-023_SECURE_RUNTIME_BASELINE',
        Rationale:
          'ADR-023 defines the v0.2 serving runtime as eu-west-2 with NetworkMode=VPC and no eu-west-1 fallback.',
        Controls: [
          'Runtime ENIs are attached to isolated platform VPC subnets',
          'Security group egress is limited to platform interface endpoints and VPC DNS',
          'Tenant execution roles authorize only the serving runtime region',
        ],
      },
    };

    const runtimeEndpoint = new cdk.CfnResource(this, 'AgentCoreRuntimeEndpoint', {
      type: 'AWS::BedrockAgentCore::RuntimeEndpoint',
      properties: {
        Name: runtimeEndpointName,
        Description: `Live endpoint for ${runtimeName}`,
        AgentRuntimeId: runtime.getAtt('AgentRuntimeId').toString(),
        AgentRuntimeVersion: runtime.getAtt('AgentRuntimeVersion').toString(),
        Tags: {
          component: 'agentcore-runtime-endpoint',
          environment: envName,
        },
      },
    });
    runtimeEndpoint.addDependency(runtime);

    const memoryTemplateParameter = new ssm.StringParameter(this, 'TenantMemoryTemplateParameter', {
      parameterName: TENANT_MEMORY_TEMPLATE_PARAMETER_NAME,
      description:
        'Template used by TenantStack when provisioning per-tenant AWS::BedrockAgentCore::Memory resources',
      stringValue: serializeAgentCoreTenantMemoryTemplate(),
      tier: ssm.ParameterTier.STANDARD,
    });

    new ssm.StringParameter(this, 'EntraJwksUrlParameter', {
      parameterName: '/platform/auth/jwks-url',
      description: 'Entra JWKS URL consumed by platform identity and runtime integrations',
      stringValue: entra.jwksUrl,
      tier: ssm.ParameterTier.STANDARD,
    });

    new cdk.CfnOutput(this, 'AgentCoreRuntimeRegion', {
      value: runtimeRegion,
      description: 'Runtime compute region for AgentCore execution',
    });
    new cdk.CfnOutput(this, 'AgentCoreRuntimeNetworkMode', {
      value: props.runtimeNetworkMode,
      description: 'ADR-023 runtime network mode',
    });
    new cdk.CfnOutput(this, 'AgentCoreRuntimeNetworkPostureDecision', {
      value: 'ADR-023_VPC',
      description: 'Explicit network posture decision guarding against silent runtime network drift',
    });
    new cdk.CfnOutput(this, 'AgentCoreRuntimeName', {
      value: runtimeName,
    });
    new cdk.CfnOutput(this, 'AgentCoreRuntimeEndpointName', {
      value: runtimeEndpointName,
    });
    new cdk.CfnOutput(this, 'TenantMemoryTemplateParameterName', {
      value: memoryTemplateParameter.parameterName,
    });
    new cdk.CfnOutput(this, 'EntraJwksUrl', {
      value: entra.jwksUrl,
    });
  }

  private requiredContext(name: string): string {
    const value = this.node.tryGetContext(name);
    if (typeof value !== 'string' || value.trim() === '') {
      throw new Error(`CDK context "${name}" is required`);
    }
    return value;
  }

  private runtimeName(envName: string): string {
    const clean = envName.replace(/[^a-zA-Z0-9]/g, '');
    return `Platform${clean}Runtime`;
  }

  private runtimeEndpointName(envName: string): string {
    const clean = envName.replace(/[^a-zA-Z0-9]/g, '');
    return `Platform${clean}Endpoint`;
  }
}
