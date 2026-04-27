import * as cdk from 'aws-cdk-lib';
import { resolveAppConfigExtensionLayerArn } from '../lib/platform-compute';

describe('platform compute AppConfig extension layer resolution', () => {
  test('fails clearly when the AppConfig extension layer ARN context is missing', () => {
    const app = new cdk.App();
    const stack = new cdk.Stack(app, 'PlatformComputeLayerArnTest', {
      env: {
        account: '123456789012',
        region: 'eu-west-2',
      },
    });

    expect(() => resolveAppConfigExtensionLayerArn(stack)).toThrow(
      'No AppConfig extension layer ARN configured for region eu-west-2. Set CDK context "appConfigExtensionLayerArn" explicitly.',
    );
  });

  test('fails clearly when the AppConfig extension layer ARN context is blank', () => {
    const app = new cdk.App({
      context: {
        appConfigExtensionLayerArn: '   ',
      },
    });
    const stack = new cdk.Stack(app, 'PlatformComputeLayerArnBlankTest', {
      env: {
        account: '123456789012',
        region: 'eu-west-2',
      },
    });

    expect(() => resolveAppConfigExtensionLayerArn(stack)).toThrow(
      'No AppConfig extension layer ARN configured for region eu-west-2. Set CDK context "appConfigExtensionLayerArn" explicitly.',
    );
  });

  test('allows an explicit context override for the AppConfig extension layer ARN', () => {
    const app = new cdk.App({
      context: {
        appConfigExtensionLayerArn:
          'arn:aws:lambda:eu-west-2:111122223333:layer:Custom-AppConfig-Extension-Arm64:7',
      },
    });
    const stack = new cdk.Stack(app, 'PlatformComputeLayerArnOverrideTest', {
      env: {
        account: '123456789012',
        region: 'eu-west-2',
      },
    });

    expect(resolveAppConfigExtensionLayerArn(stack)).toBe(
      'arn:aws:lambda:eu-west-2:111122223333:layer:Custom-AppConfig-Extension-Arm64:7',
    );
  });
});
