import * as cdk from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { PlatformEdgeSecurityStack } from '../lib/platform-edge-security-stack';

describe('PlatformEdgeSecurityStack', () => {
  const synthStack = () => {
    const app = new cdk.App({
      context: {
        env: 'dev',
      },
    });

    const stack = new PlatformEdgeSecurityStack(app, 'platform-edge-security-dev', {
      env: {
        account: '123456789012',
        region: 'us-east-1',
      },
      envName: 'dev',
    });

    return Template.fromStack(stack);
  };

  test('creates a CloudFront-scoped SPA web ACL with managed baseline rules', () => {
    const template = synthStack();

    template.hasResourceProperties('AWS::WAFv2::WebACL', {
      Scope: 'CLOUDFRONT',
      Rules: Match.arrayWith([
        Match.objectLike({
          Name: 'AWSManagedRulesAmazonIpReputationList',
        }),
        Match.objectLike({
          Name: 'AWSManagedRulesCommonRuleSet',
          OverrideAction: {
            Count: {},
          },
        }),
        Match.objectLike({
          Name: 'AWSManagedRulesKnownBadInputsRuleSet',
          OverrideAction: {
            Count: {},
          },
        }),
      ]),
    });
  });

  test('creates blocked and challenge alarms for the SPA edge WAF', () => {
    const template = synthStack();

    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'platform-edge-security-dev-SPA-WAF-BlockedRequests',
      MetricName: 'BlockedRequests',
      Namespace: 'AWS/WAFV2',
    });

    template.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'platform-edge-security-dev-SPA-WAF-ChallengeRequests',
      MetricName: 'ChallengeRequests',
      Namespace: 'AWS/WAFV2',
    });
  });
});
