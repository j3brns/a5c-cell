import * as cdk from 'aws-cdk-lib';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as wafv2 from 'aws-cdk-lib/aws-wafv2';
import { Construct } from 'constructs';

export interface PlatformWafProps {
  readonly api: apigateway.RestApi;
}

export class PlatformWaf extends Construct {
  public readonly apiWebAcl: wafv2.CfnWebACL;

  constructor(scope: Construct, id: string, props: PlatformWafProps) {
    super(scope, id);

    const webAclMetricName = `${cdk.Stack.of(this).stackName}-api-waf`;

    this.apiWebAcl = new wafv2.CfnWebACL(this, 'ApiWebAcl', {
      name: `${cdk.Stack.of(this).stackName}-api-waf`,
      defaultAction: { allow: {} },
      scope: 'REGIONAL',
      visibilityConfig: {
        cloudWatchMetricsEnabled: true,
        metricName: webAclMetricName,
        sampledRequestsEnabled: true,
      },
      rules: [
        {
          name: 'AWSManagedRulesCommonRuleSet',
          priority: 0,
          overrideAction: { none: {} },
          statement: {
            managedRuleGroupStatement: {
              vendorName: 'AWS',
              name: 'AWSManagedRulesCommonRuleSet',
            },
          },
          visibilityConfig: {
            cloudWatchMetricsEnabled: true,
            metricName: 'aws-managed-common',
            sampledRequestsEnabled: true,
          },
        },
        {
          name: 'AWSManagedRulesAmazonIpReputationList',
          priority: 1,
          overrideAction: { count: {} },
          statement: {
            managedRuleGroupStatement: {
              vendorName: 'AWS',
              name: 'AWSManagedRulesAmazonIpReputationList',
            },
          },
          visibilityConfig: {
            cloudWatchMetricsEnabled: true,
            metricName: 'aws-managed-amazon-ip-reputation-count',
            sampledRequestsEnabled: true,
          },
        },
        {
          name: 'AWSManagedRulesKnownBadInputsRuleSet',
          priority: 2,
          overrideAction: { count: {} },
          statement: {
            managedRuleGroupStatement: {
              vendorName: 'AWS',
              name: 'AWSManagedRulesKnownBadInputsRuleSet',
            },
          },
          visibilityConfig: {
            cloudWatchMetricsEnabled: true,
            metricName: 'aws-managed-known-bad-inputs-count',
            sampledRequestsEnabled: true,
          },
        },
        {
          name: 'GlobalIpRateLimit',
          priority: 3,
          action: { block: {} },
          statement: {
            rateBasedStatement: {
              aggregateKeyType: 'IP',
              limit: 10000,
            },
          },
          visibilityConfig: {
            cloudWatchMetricsEnabled: true,
            metricName: 'global-ip-rate-limit',
            sampledRequestsEnabled: true,
          },
        },
        {
          name: 'UkIpRateLimit',
          priority: 4,
          action: { block: {} },
          statement: {
            rateBasedStatement: {
              aggregateKeyType: 'IP',
              limit: 2000,
              scopeDownStatement: {
                geoMatchStatement: {
                  countryCodes: ['GB'],
                },
              },
            },
          },
          visibilityConfig: {
            cloudWatchMetricsEnabled: true,
            metricName: 'uk-ip-rate-limit',
            sampledRequestsEnabled: true,
          },
        },
        {
          name: 'BlockSqlmapUserAgent',
          priority: 5,
          action: { block: {} },
          statement: {
            byteMatchStatement: {
              fieldToMatch: {
                singleHeader: {
                  Name: 'user-agent',
                },
              },
              positionalConstraint: 'CONTAINS',
              searchString: 'sqlmap',
              textTransformations: [
                {
                  priority: 0,
                  type: 'LOWERCASE',
                },
              ],
            },
          },
          visibilityConfig: {
            cloudWatchMetricsEnabled: true,
            metricName: 'block-sqlmap-user-agent',
            sampledRequestsEnabled: true,
          },
        },
      ],
    });

    new wafv2.CfnWebACLAssociation(this, 'ApiWebAclAssociation', {
      resourceArn: props.api.deploymentStage.stageArn,
      webAclArn: this.apiWebAcl.attrArn,
    });
  }
}
