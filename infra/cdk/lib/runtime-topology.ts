export const HOME_REGION = 'eu-west-2';
export const SERVING_RUNTIME_REGION = HOME_REGION;
export const EDGE_REGION = 'us-east-1';
export const EVALUATION_REGION = 'eu-central-1';

export const AUTHORIZED_RUNTIME_REGIONS = [SERVING_RUNTIME_REGION] as const;
export const RUNTIME_NETWORK_MODE = 'VPC' as const;
