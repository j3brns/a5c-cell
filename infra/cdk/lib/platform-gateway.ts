import * as cdk from 'aws-cdk-lib';
import * as cr from 'aws-cdk-lib/custom-resources';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import { Construct } from 'constructs';

export interface PlatformGatewayProps {
  readonly enforcementMode: 'LOG_ONLY' | 'ENFORCE';
  readonly policyEngineName: string;
  readonly policyName: string;
  readonly requestInterceptorFn: lambda.IFunction;
  readonly responseInterceptorFn: lambda.IFunction;
  readonly diagnosticsToolFn: lambda.IFunction;
  readonly toolsTable: dynamodb.ITable;
}

export class PlatformGateway extends Construct {
  public readonly enforcementMode: 'LOG_ONLY' | 'ENFORCE';

  constructor(scope: Construct, id: string, props: PlatformGatewayProps) {
    super(scope, id);
    this.enforcementMode = props.enforcementMode;

    const agentCoreGatewayRole = new iam.Role(this, 'AgentCoreGatewayExecutionRole', {
      assumedBy: new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com'),
      description: 'Execution role for AgentCore Gateway interceptors',
    });

    const gatewayPolicyEngine = new cdk.CfnResource(this, 'AgentCoreGatewayPolicyEngine', {
      type: 'AWS::BedrockAgentCore::PolicyEngine',
      properties: {
        Name: props.policyEngineName,
        Description: 'Cedar policy engine for AgentCore Gateway tool authorization',
        Tags: [
          {
            Key: 'stack',
            Value: cdk.Stack.of(this).stackName,
          },
          {
            Key: 'component',
            Value: 'platform-gateway-policy',
          },
        ],
      },
    });

    agentCoreGatewayRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['lambda:InvokeFunction'],
        resources: [
          props.requestInterceptorFn.functionArn,
          props.responseInterceptorFn.functionArn,
          props.diagnosticsToolFn.functionArn,
        ],
      }),
    );
    const agentCoreGateway = new cdk.CfnResource(this, 'AgentCoreGateway', {
      type: 'AWS::BedrockAgentCore::Gateway',
      properties: {
        Name: `${cdk.Stack.of(this).stackName.toLowerCase().replace(/[^a-z0-9-]/g, '-')}-gateway`,
        Description: 'Platform AgentCore Gateway with request/response interceptors',
        AuthorizerType: 'AWS_IAM',
        ProtocolType: 'MCP',
        RoleArn: agentCoreGatewayRole.roleArn,
        PolicyEngineConfiguration: {
          Arn: gatewayPolicyEngine.ref,
          Mode: props.enforcementMode,
        },
        InterceptorConfigurations: [
          {
            InterceptionPoints: ['REQUEST'],
            InputConfiguration: {
              PassRequestHeaders: true,
            },
            Interceptor: {
              Lambda: {
                Arn: props.requestInterceptorFn.functionArn,
              },
            },
          },
          {
            InterceptionPoints: ['RESPONSE'],
            InputConfiguration: {
              PassRequestHeaders: true,
            },
            Interceptor: {
              Lambda: {
                Arn: props.responseInterceptorFn.functionArn,
              },
            },
          },
        ],
        Tags: {
          stack: cdk.Stack.of(this).stackName,
          component: 'platform-gateway',
        },
      },
    });

    agentCoreGatewayRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['bedrock-agentcore:GetPolicyEngine'],
        resources: [gatewayPolicyEngine.ref],
      }),
    );
    agentCoreGatewayRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ['bedrock-agentcore:AuthorizeAction', 'bedrock-agentcore:PartiallyAuthorizeActions'],
        resources: [gatewayPolicyEngine.ref, agentCoreGateway.getAtt('GatewayArn').toString()],
      }),
    );

    const cedarStatement = cdk.Fn.sub(
      [
        'permit (',
        '  principal is AgentCore::IamEntity,',
        '  action,',
        '  resource == AgentCore::Gateway::"${GatewayArn}"',
        ') when {',
        '  principal.id like "arn:aws:sts::${AWS::AccountId}:assumed-role/platform-tenant-*-execution-role/*" ||',
        '  principal.id like "arn:aws:iam::${AWS::AccountId}:role/platform-tenant-*-execution-role"',
        '};',
      ].join('\n'),
      {
        GatewayArn: agentCoreGateway.getAtt('GatewayArn').toString(),
      },
    );

    const gatewayDefaultPolicy = new cdk.CfnResource(this, 'AgentCoreGatewayDefaultPolicy', {
      type: 'AWS::BedrockAgentCore::Policy',
      properties: {
        Name: props.policyName,
        Description: 'Deny-by-default Cedar policy for tenant execution-role Gateway access',
        PolicyEngineId: gatewayPolicyEngine.getAtt('PolicyEngineId').toString(),
        ValidationMode: 'FAIL_ON_ANY_FINDINGS',
        Definition: {
          Cedar: {
            Statement: cedarStatement,
          },
        },
      },
    });
    gatewayDefaultPolicy.addDependency(agentCoreGateway);

    const diagnosticsTarget = new cdk.CfnResource(this, 'PlatformDiagnosticsGatewayTarget', {
      type: 'AWS::BedrockAgentCore::GatewayTarget',
      properties: {
        Name: 'platform-diagnostics',
        Description: 'Read-only platform diagnostics MCP tools',
        GatewayIdentifier: agentCoreGateway.ref,
        MetadataConfiguration: {
          AllowedRequestHeaders: ['x-tenant-id', 'x-app-id'],
        },
        CredentialProviderConfigurations: [
          {
            CredentialProviderType: 'GATEWAY_IAM_ROLE',
            CredentialProvider: {
              IamCredentialProvider: {
                Service: 'lambda',
                Region: cdk.Stack.of(this).region,
              },
            },
          },
        ],
        TargetConfiguration: {
          Mcp: {
            Lambda: {
              LambdaArn: props.diagnosticsToolFn.functionArn,
              ToolSchema: {
                InlinePayload: platformDiagnosticsToolSchema(),
              },
            },
          },
        },
      },
    });
    diagnosticsTarget.addDependency(agentCoreGateway);

    for (const tool of platformDiagnosticsToolSchema()) {
      const registryRecord = new cr.AwsCustomResource(this, `RegistryRecord${toPascalCase(tool.Name)}`, {
        installLatestAwsSdk: false,
        onCreate: putToolRegistryCall({
          tableName: props.toolsTable.tableName,
          toolName: tool.Name,
          lambdaArn: props.diagnosticsToolFn.functionArn,
          gatewayTargetId: diagnosticsTarget.getAtt('TargetId').toString(),
        }),
        onUpdate: putToolRegistryCall({
          tableName: props.toolsTable.tableName,
          toolName: tool.Name,
          lambdaArn: props.diagnosticsToolFn.functionArn,
          gatewayTargetId: diagnosticsTarget.getAtt('TargetId').toString(),
        }),
        onDelete: {
          service: 'DynamoDB',
          action: 'deleteItem',
          parameters: {
            TableName: props.toolsTable.tableName,
            Key: toolRegistryKey(tool.Name),
          },
          physicalResourceId: cr.PhysicalResourceId.of(
            `platform-diagnostics-tool-registry-${tool.Name}`,
          ),
        },
        policy: cr.AwsCustomResourcePolicy.fromSdkCalls({
          resources: [props.toolsTable.tableArn],
        }),
      });
      registryRecord.node.addDependency(diagnosticsTarget);
    }
  }
}

function toolRegistryKey(toolName: string) {
  return {
    PK: { S: `TOOL#${toolName}` },
    SK: { S: 'TENANT#platform' },
  };
}

function putToolRegistryCall(props: {
  tableName: string;
  toolName: string;
  lambdaArn: string;
  gatewayTargetId: string;
}): cr.AwsSdkCall {
  return {
    service: 'DynamoDB',
    action: 'putItem',
    parameters: {
      TableName: props.tableName,
      Item: {
        ...toolRegistryKey(props.toolName),
        tool_name: { S: props.toolName },
        tier_minimum: { S: 'premium' },
        lambda_arn: { S: props.lambdaArn },
        gateway_target_id: { S: props.gatewayTargetId },
        enabled: { BOOL: true },
      },
    },
    physicalResourceId: cr.PhysicalResourceId.of(
      `platform-diagnostics-tool-registry-${props.toolName}`,
    ),
  };
}

function toPascalCase(value: string): string {
  return value
    .split(/[^a-zA-Z0-9]+/)
    .filter(Boolean)
    .map((part) => `${part.charAt(0).toUpperCase()}${part.slice(1)}`)
    .join('');
}

function emptyObjectSchema() {
  return {
    Type: 'object',
    Properties: {},
    Required: [],
  };
}

function tenantIdSchema() {
  return {
    Type: 'object',
    Properties: {
      tenant_id: {
        Type: 'string',
        Description: 'Tenant ID to inspect.',
      },
    },
    Required: ['tenant_id'],
  };
}

function optionalTenantIdSchema() {
  return {
    Type: 'object',
    Properties: {
      tenant_id: {
        Type: 'string',
        Description: 'Optional tenant ID filter.',
      },
    },
    Required: [],
  };
}

function runbookGuidanceSchema() {
  return {
    Type: 'object',
    Properties: {
      query: {
        Type: 'string',
        Description: 'Optional keyword search across operator runbooks.',
      },
      runbook_id: {
        Type: 'string',
        Description: 'Optional runbook identifier such as RUNBOOK-001.',
      },
    },
    Required: [],
  };
}

function platformDiagnosticsToolSchema() {
  return [
    {
      Name: 'get_platform_health',
      Description: 'Return read-only health signals for the platform serving path.',
      InputSchema: emptyObjectSchema(),
    },
    {
      Name: 'get_tenant_status',
      Description: 'Return read-only tenant status and recent invocation summary.',
      InputSchema: tenantIdSchema(),
    },
    {
      Name: 'get_recent_errors',
      Description: 'Return recent platform errors or security events.',
      InputSchema: optionalTenantIdSchema(),
    },
    {
      Name: 'get_runbook_guidance',
      Description: 'Return operator runbook guidance by ID or query.',
      InputSchema: runbookGuidanceSchema(),
    },
  ];
}
