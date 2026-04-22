import * as cdk from 'aws-cdk-lib';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as wafv2 from 'aws-cdk-lib/aws-wafv2';

export interface PlatformEdgeSecurityStackProps extends cdk.StackProps {
  readonly envName: string;
}

export class PlatformEdgeSecurityStack extends cdk.Stack {
  public readonly spaWebAcl: wafv2.CfnWebACL;

  constructor(scope: cdk.App, id: string, props: PlatformEdgeSecurityStackProps) {
    super(scope, id, props);

    const webAclMetricName = `${this.stackName}-spa-edge-waf`;

    this.spaWebAcl = new wafv2.CfnWebACL(this, 'SpaEdgeWebAcl', {
      name: `${this.stackName}-spa-edge-waf`,
      defaultAction: { allow: {} },
      scope: 'CLOUDFRONT',
      visibilityConfig: {
        cloudWatchMetricsEnabled: true,
        metricName: webAclMetricName,
        sampledRequestsEnabled: true,
      },
      rules: [
        {
          name: 'AWSManagedRulesAmazonIpReputationList',
          priority: 0,
          overrideAction: { none: {} },
          statement: {
            managedRuleGroupStatement: {
              vendorName: 'AWS',
              name: 'AWSManagedRulesAmazonIpReputationList',
            },
          },
          visibilityConfig: {
            cloudWatchMetricsEnabled: true,
            metricName: 'aws-managed-ip-reputation',
            sampledRequestsEnabled: true,
          },
        },
        {
          name: 'AWSManagedRulesCommonRuleSet',
          priority: 1,
          overrideAction: { count: {} },
          statement: {
            managedRuleGroupStatement: {
              vendorName: 'AWS',
              name: 'AWSManagedRulesCommonRuleSet',
            },
          },
          visibilityConfig: {
            cloudWatchMetricsEnabled: true,
            metricName: 'aws-managed-common-count',
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
      ],
    });

    const blockedMetric = new cloudwatch.Metric({
      namespace: 'AWS/WAFV2',
      metricName: 'BlockedRequests',
      dimensionsMap: {
        WebACL: webAclMetricName,
        Rule: 'ALL',
      },
      statistic: 'Sum',
      region: 'us-east-1',
    });

    const challengedMetric = new cloudwatch.Metric({
      namespace: 'AWS/WAFV2',
      metricName: 'ChallengeRequests',
      dimensionsMap: {
        WebACL: webAclMetricName,
        Rule: 'ALL',
      },
      statistic: 'Sum',
      region: 'us-east-1',
    });

    new cloudwatch.Alarm(this, 'SpaEdgeBlockedRequestsAlarm', {
      alarmName: `${this.stackName}-SPA-WAF-BlockedRequests`,
      alarmDescription: 'Blocked requests detected for the SPA CloudFront edge WAF',
      metric: blockedMetric,
      threshold: 100,
      evaluationPeriods: 1,
      datapointsToAlarm: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    new cloudwatch.Alarm(this, 'SpaEdgeChallengeRequestsAlarm', {
      alarmName: `${this.stackName}-SPA-WAF-ChallengeRequests`,
      alarmDescription: 'Challenge requests detected for the SPA CloudFront edge WAF',
      metric: challengedMetric,
      threshold: 1,
      evaluationPeriods: 1,
      datapointsToAlarm: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    new cdk.CfnOutput(this, 'SpaWebAclArn', {
      value: this.spaWebAcl.attrArn,
      description: 'ARN of the us-east-1 CloudFront-scoped WAF web ACL for the SPA distribution',
    });

    new cdk.CfnOutput(this, 'SpaWebAclMetricName', {
      value: webAclMetricName,
      description: 'CloudWatch metric name for the SPA edge WAF web ACL',
    });
  }
}
