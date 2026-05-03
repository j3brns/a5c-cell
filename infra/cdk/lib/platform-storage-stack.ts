import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import { Construct } from 'constructs';
import { createPlatformStorage, PlatformStorageResources } from './platform-storage';

export interface PlatformStorageStackProps extends cdk.StackProps {
  readonly envName: string;
  readonly vpc: ec2.IVpc;
}

export class PlatformStorageStack extends cdk.Stack {
  public readonly storage: PlatformStorageResources;
  public readonly bridgeValkeyClientSecurityGroup: ec2.SecurityGroup;

  constructor(scope: Construct, id: string, props: PlatformStorageStackProps) {
    super(scope, id, props);

    this.bridgeValkeyClientSecurityGroup = new ec2.SecurityGroup(
      this,
      'BridgeValkeyClientSecurityGroup',
      {
        vpc: props.vpc,
        allowAllOutbound: false,
        description: 'Bridge Lambda client access to platform Valkey',
      },
    );

    this.storage = createPlatformStorage(this, {
      envName: props.envName,
      vpc: props.vpc,
      valkeyClientSecurityGroup: this.bridgeValkeyClientSecurityGroup,
    });

    // Add egress rule to client SG pointing to storage SG
    this.bridgeValkeyClientSecurityGroup.addEgressRule(
      this.storage.valkeySecurityGroup,
      ec2.Port.tcp(6379),
      'Allow Redis/Valkey egress to platform Valkey cluster',
    );

    // Export identifiers for core stack to use
    new cdk.CfnOutput(this, 'JobsTableStreamArn', {
      value: this.storage.jobsTable.tableStreamArn || '',
      exportName: `${this.stackName}-JobsTableStreamArn`,
    });
  }
}
