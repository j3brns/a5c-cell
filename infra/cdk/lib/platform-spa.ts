import * as cdk from 'aws-cdk-lib';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import { Construct } from 'constructs';

export interface PlatformSpaProps {
  readonly envName: string;
  readonly spaDomainName?: string;
  readonly spaCertificateArn?: string;
  readonly spaWebAclArn?: string;
  readonly apiAllowedOrigin?: string;
  readonly entraAuthorityOrigin: string;
  readonly agUiAllowedOrigins?: string[];
}

const LOCAL_DEV_CONNECT_SRC_ORIGINS = [
  'http://localhost:3000',
  'http://localhost:4566',
  'http://localhost:8080',
];
const MAX_CONTENT_SECURITY_POLICY_LENGTH = 1783;

function parseAcmCertificateArn(certificateArn: string): { region: string; account: string } | null {
  const match = /^arn:[^:]+:acm:([^:]+):([^:]+):certificate\/.+$/.exec(certificateArn);
  if (!match) {
    return null;
  }

  return {
    region: match[1],
    account: match[2],
  };
}

function buildConnectSrcDirective(
  props: PlatformSpaProps,
): string {
  const connectSrcOrigins = new Set<string>(["'self'", props.entraAuthorityOrigin]);

  if (props.spaDomainName) {
    connectSrcOrigins.add(`https://${props.spaDomainName}`);
  }

  if (props.apiAllowedOrigin) {
    connectSrcOrigins.add(props.apiAllowedOrigin);
  }

  for (const origin of props.agUiAllowedOrigins ?? []) {
    connectSrcOrigins.add(origin);
  }

  if (props.envName !== 'prod') {
    for (const origin of LOCAL_DEV_CONNECT_SRC_ORIGINS) {
      connectSrcOrigins.add(origin);
    }
  }

  return `connect-src ${Array.from(connectSrcOrigins).join(' ')}`;
}

function buildContentSecurityPolicy(props: PlatformSpaProps): string {
  const contentSecurityPolicy =
    `default-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; ${buildConnectSrcDirective(props)}; ` +
    "img-src 'self' data: https:; style-src 'self' 'unsafe-inline'; script-src 'self';";

  if (contentSecurityPolicy.length > MAX_CONTENT_SECURITY_POLICY_LENGTH) {
    throw new Error(
      `SPA Content-Security-Policy exceeds the CloudFront response headers policy limit of ${MAX_CONTENT_SECURITY_POLICY_LENGTH} characters`,
    );
  }

  return contentSecurityPolicy;
}

export class PlatformSpa extends Construct {
  public readonly spaDistribution: cloudfront.CfnDistribution;
  public readonly spaAllowedOrigin: string;

  constructor(scope: Construct, id: string, props: PlatformSpaProps) {
    super(scope, id);

    const spaDomainNameProvided = props.spaDomainName !== undefined;
    const spaCertificateArnProvided = props.spaCertificateArn !== undefined;
    const spaDomainName = props.spaDomainName?.trim();
    const spaCertificateArn = props.spaCertificateArn?.trim();
    const hasSpaDomainName = typeof spaDomainName === 'string' && spaDomainName.length > 0;
    const hasSpaCertificateArn = typeof spaCertificateArn === 'string' && spaCertificateArn.length > 0;

    if (hasSpaDomainName !== hasSpaCertificateArn) {
      throw new Error(
        'Custom SPA domain configuration requires both spaDomainName and spaCertificateArn to be set together.',
      );
    }

    if ((spaDomainNameProvided && !hasSpaDomainName) || (spaCertificateArnProvided && !hasSpaCertificateArn)) {
      throw new Error('spaDomainName and spaCertificateArn must not be blank when provided.');
    }

    if (hasSpaCertificateArn && spaCertificateArn) {
      const parsedCertificateArn = parseAcmCertificateArn(spaCertificateArn);
      if (!parsedCertificateArn) {
        throw new Error(
          'spaCertificateArn must be an ACM certificate ARN in the form arn:aws:acm:us-east-1:<account>:certificate/<id>.',
        );
      }

      if (parsedCertificateArn.region !== 'us-east-1') {
        throw new Error('spaCertificateArn must reference an ACM certificate in us-east-1 for CloudFront.');
      }

      const stackAccount = cdk.Stack.of(this).account;
      if (cdk.Token.isUnresolved(stackAccount)) {
        throw new Error('spaCertificateArn requires a concrete stack account so same-account ownership can be enforced.');
      }

      if (parsedCertificateArn.account !== stackAccount) {
        throw new Error(
          `spaCertificateArn account ${parsedCertificateArn.account} must match the stack account ${stackAccount}.`,
        );
      }
    }

    const spaBucket = new s3.Bucket(this, 'SpaBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      versioned: true,
    });

    const isProd = props.envName === 'prod';
    const retentionDays = isProd ? 365 : 30;

    const spaLogBucket = new s3.Bucket(this, 'SpaLogBucket', {
      bucketName: `platform-spa-logs-${props.envName}`,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
      objectOwnership: s3.ObjectOwnership.BUCKET_OWNER_PREFERRED,
      accessControl: s3.BucketAccessControl.LOG_DELIVERY_WRITE,
      lifecycleRules: [
        {
          expiration: cdk.Duration.days(retentionDays),
          id: 'RetentionRule',
        },
      ],
    });

    const spaResponseHeadersPolicy = new cloudfront.CfnResponseHeadersPolicy(
      this,
      'SpaCspResponseHeadersPolicy',
      {
        responseHeadersPolicyConfig: {
          name: `${cdk.Stack.of(this).stackName}-spa-security-headers`,
          comment: 'Security headers for platform SPA',
          securityHeadersConfig: {
            contentSecurityPolicy: {
              contentSecurityPolicy: buildContentSecurityPolicy(props),
              override: true,
            },
            frameOptions: {
              frameOption: 'DENY',
              override: true,
            },
            strictTransportSecurity: {
              accessControlMaxAgeSec: 31536000,
              includeSubdomains: true,
              preload: true,
              override: true,
            },
            contentTypeOptions: {
              override: true,
            },
            referrerPolicy: {
              referrerPolicy: 'same-origin',
              override: true,
            },
            xssProtection: {
              protection: true,
              modeBlock: true,
              override: true,
            },
          },
        },
      },
    );

    const spaOriginAccessControl = new cloudfront.CfnOriginAccessControl(this, 'SpaOriginAccessControl', {
      originAccessControlConfig: {
        name: `${cdk.Stack.of(this).stackName}-spa-oac`,
        description: 'OAC for SPA bucket origin',
        originAccessControlOriginType: 's3',
        signingBehavior: 'always',
        signingProtocol: 'sigv4',
      },
    });

    const spaRouteRewriteFunction = new cloudfront.Function(this, 'SpaRouteRewriteFunction', {
      comment: 'Rewrite SPA deep links to index.html without masking missing asset failures',
      code: cloudfront.FunctionCode.fromInline(`
function handler(event) {
  var request = event.request;
  var uri = request.uri || '/';
  var lastSegment = uri.substring(uri.lastIndexOf('/') + 1);

  if (uri === '/' || lastSegment.indexOf('.') === -1) {
    request.uri = '/index.html';
  }

  return request;
}
      `),
    });

    this.spaDistribution = new cloudfront.CfnDistribution(this, 'SpaDistribution', {
      distributionConfig: {
        enabled: true,
        comment: 'Platform SPA distribution',
        defaultRootObject: 'index.html',
        httpVersion: 'http2',
        priceClass: 'PriceClass_100',
        ipv6Enabled: true,
        logging: {
          bucket: spaLogBucket.bucketRegionalDomainName,
          includeCookies: false,
          prefix: 'spa-cloudfront/',
        },
        origins: [
          {
            id: 'SpaS3Origin',
            domainName: spaBucket.bucketRegionalDomainName,
            originAccessControlId: spaOriginAccessControl.attrId,
            s3OriginConfig: {
              originAccessIdentity: '',
            },
          },
        ],
        defaultCacheBehavior: {
          targetOriginId: 'SpaS3Origin',
          viewerProtocolPolicy: 'redirect-to-https',
          compress: true,
          allowedMethods: ['GET', 'HEAD', 'OPTIONS'],
          cachedMethods: ['GET', 'HEAD', 'OPTIONS'],
          cachePolicyId: cloudfront.CachePolicy.CACHING_DISABLED.cachePolicyId,
          responseHeadersPolicyId: spaResponseHeadersPolicy.attrId,
          functionAssociations: [
            {
              eventType: 'viewer-request',
              functionArn: spaRouteRewriteFunction.functionArn,
            },
          ],
        },
        cacheBehaviors: [
          {
            pathPattern: 'assets/*',
            targetOriginId: 'SpaS3Origin',
            viewerProtocolPolicy: 'redirect-to-https',
            compress: true,
            allowedMethods: ['GET', 'HEAD', 'OPTIONS'],
            cachedMethods: ['GET', 'HEAD', 'OPTIONS'],
            cachePolicyId: cloudfront.CachePolicy.CACHING_OPTIMIZED.cachePolicyId,
            responseHeadersPolicyId: spaResponseHeadersPolicy.attrId,
          },
        ],
        restrictions: {
          geoRestriction: {
            restrictionType: 'none',
          },
        },
        ...(props.spaWebAclArn
          ? {
              webAclId: props.spaWebAclArn,
            }
          : {}),
        ...(hasSpaDomainName && hasSpaCertificateArn && spaDomainName && spaCertificateArn
          ? {
              aliases: [spaDomainName],
              viewerCertificate: {
                acmCertificateArn: spaCertificateArn,
                minimumProtocolVersion: 'TLSv1.2_2021',
                sslSupportMethod: 'sni-only',
              },
            }
          : {
              viewerCertificate: {
                cloudFrontDefaultCertificate: true,
                minimumProtocolVersion: 'TLSv1.2_2021',
              },
            }),
      },
    });

    spaBucket.addToResourcePolicy(
      new iam.PolicyStatement({
        sid: 'AllowCloudFrontOacRead',
        effect: iam.Effect.ALLOW,
        principals: [new iam.ServicePrincipal('cloudfront.amazonaws.com')],
        actions: ['s3:GetObject'],
        resources: [spaBucket.arnForObjects('*')],
        conditions: {
          StringEquals: {
            'AWS:SourceArn': cdk.Fn.join('', [
              'arn:',
              cdk.Aws.PARTITION,
              ':cloudfront::',
              cdk.Aws.ACCOUNT_ID,
              ':distribution/',
              this.spaDistribution.ref,
            ]),
          },
        },
      }),
    );

    new ssm.StringParameter(this, 'SpaBucketNameParam', {
      parameterName: `/platform/spa/${props.envName}/bucket-name`,
      stringValue: spaBucket.bucketName,
      description: 'S3 bucket name for the platform SPA',
    });

    new ssm.StringParameter(this, 'SpaDistributionIdParam', {
      parameterName: `/platform/spa/${props.envName}/distribution-id`,
      stringValue: this.spaDistribution.ref,
      description: 'CloudFront distribution ID for the platform SPA',
    });

    const spaBucketNameOutput = new cdk.CfnOutput(this, 'SpaBucketName', {
      value: spaBucket.bucketName,
      description: 'S3 bucket name for the platform SPA',
    });
    spaBucketNameOutput.overrideLogicalId('SpaBucketName');

    const spaDistributionIdOutput = new cdk.CfnOutput(this, 'SpaDistributionId', {
      value: this.spaDistribution.ref,
      description: 'CloudFront distribution ID for the platform SPA',
    });
    spaDistributionIdOutput.overrideLogicalId('SpaDistributionId');

    if (hasSpaDomainName && spaDomainName) {
      new ssm.StringParameter(this, 'SpaDomainNameParam', {
        parameterName: `/platform/spa/${props.envName}/domain-name`,
        stringValue: spaDomainName,
        description: 'Custom domain name for the platform SPA CloudFront distribution',
      });

      const spaDomainNameOutput = new cdk.CfnOutput(this, 'SpaDomainName', {
        value: spaDomainName,
        description: 'Custom domain name for the platform SPA',
      });
      spaDomainNameOutput.overrideLogicalId('SpaDomainName');
    }

    this.spaAllowedOrigin = hasSpaDomainName && spaDomainName
      ? `https://${spaDomainName}`
      : cdk.Fn.join('', ['https://', this.spaDistribution.attrDomainName]);
  }
}
