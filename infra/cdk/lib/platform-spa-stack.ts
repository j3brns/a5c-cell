import * as cdk from 'aws-cdk-lib';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import { Construct } from 'constructs';
import { PlatformSpa } from './platform-spa';
import { resolveEntraConfiguration } from './entra-config';

export interface PlatformSpaStackProps extends cdk.StackProps {
  readonly envName: string;
  readonly apiDomainName?: string;
  readonly agUiEndpointOrigins: string[];
}

export class PlatformSpaStack extends cdk.Stack {
  public readonly spaDistribution: cloudfront.CfnDistribution;
  public readonly spaAllowedOrigin: string;

  constructor(scope: Construct, id: string, props: PlatformSpaStackProps) {
    super(scope, id, props);

    const entra = resolveEntraConfiguration(this);

    const spaDomainName = this.node.tryGetContext('spaDomainName') as string | undefined;
    const spaCertificateArn = this.node.tryGetContext('spaCertificateArn') as string | undefined;
    const spaWebAclArn = this.node.tryGetContext('spaWebAclArn') as string | undefined;

    const platformSpa = new PlatformSpa(this, 'PlatformSpa', {
      envName: props.envName,
      spaDomainName,
      spaCertificateArn,
      spaWebAclArn,
      apiAllowedOrigin: props.apiDomainName ? `https://${props.apiDomainName}` : undefined,
      entraAuthorityOrigin: this.normalizeOrigin(entra.issuer, 'entraIssuer'),
      agUiAllowedOrigins: props.agUiEndpointOrigins,
    });

    this.spaDistribution = platformSpa.spaDistribution;
    this.spaAllowedOrigin = platformSpa.spaAllowedOrigin;
  }

  private normalizeOrigin(value: string, contextName: string): string {
    try {
      const parsed = new URL(value);
      if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
        throw new Error();
      }
      return parsed.origin;
    } catch {
      throw new Error(`${contextName} must be an absolute http(s) URL: ${value}`);
    }
  }
}
