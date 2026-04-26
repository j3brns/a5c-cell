import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import { Construct } from 'constructs';

export interface PlatformGatewayProps {
  readonly enforcementMode: 'LOG_ONLY' | 'ENFORCE';
  readonly policyEngineName: string;
  readonly policyName: string;
  readonly requestInterceptorFn: lambda.IFunction;
  readonly responseInterceptorFn: lambda.IFunction;
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
        resources: [props.requestInterceptorFn.functionArn, props.responseInterceptorFn.functionArn],
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
  }
}
