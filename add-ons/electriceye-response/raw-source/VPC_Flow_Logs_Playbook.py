# This file is part of ElectricEye.

# ElectricEye is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# ElectricEye is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License along with ElectricEye.  
# If not, see https://github.com/jonrau1/ElectricEye/blob/master/LICENSE.
import boto3
import os
import time
import json

def lambda_handler(event, context):
    # boto3 clients
    sts = boto3.client('sts')
    securityhub = boto3.client('securityhub')
    # create env vars
    awsRegion = os.environ['AWS_REGION']
    lambdaFunctionName = os.environ['AWS_LAMBDA_FUNCTION_NAME']
    masterAccountId = sts.get_caller_identity()['Account']
    # parse Security Hub CWE
    securityHubEvent = (event['detail']['findings'])
    for findings in securityHubEvent:
        # parse finding ID
        findingId =str(findings['Id'])
        # parse Account from SecHub Finding
        findingOwner = str(findings['AwsAccountId'])
        for resources in findings['Resources']:
            resourceId = str(resources['Id'])
            # create resource ID
            vpcId = resourceId.replace('arn:aws:ec2:' + awsRegion + ':' + findingOwner + ':vpc/', '')
            if findingOwner != masterAccountId:
                memberAcct = sts.assume_role(RoleArn='arn:aws:iam::' + findingOwner + ':role/XA-ElectricEye-Response',RoleSessionName='x_acct_sechub')
                # retrieve creds from member account
                xAcctAccessKey = memberAcct['Credentials']['AccessKeyId']
                xAcctSecretKey = memberAcct['Credentials']['SecretAccessKey']
                xAcctSeshToken = memberAcct['Credentials']['SessionToken']
                # create service client using the assumed role credentials
                logs = boto3.client('logs',aws_access_key_id=xAcctAccessKey,aws_secret_access_key=xAcctSecretKey,aws_session_token=xAcctSeshToken)
                iam = boto3.client('iam',aws_access_key_id=xAcctAccessKey,aws_secret_access_key=xAcctSecretKey,aws_session_token=xAcctSeshToken)
                ec2 = boto3.client('ec2',aws_access_key_id=xAcctAccessKey,aws_secret_access_key=xAcctSecretKey,aws_session_token=xAcctSeshToken)
                # create flow logs and wait for the creation to propogate
                try:
                    response = logs.create_log_group(logGroupName='VPCFlowLogs/' + vpcId)
                    print(response)
                    time.sleep(5)
                except Exception as e:
                    print(e)
                    raise
                # describe the created group and retrieve the ARN
                try:
                    response = logs.describe_log_groups(logGroupNamePrefix='VPCFlowLogs/' + vpcId)
                    logGroupArn = str(response['logGroups'][0]['arn'])
                    logGroupName = str(response['logGroups'][0]['logGroupName'])
                except Exception as e:
                    print(e)
                    raise
                # create a VPC Flow > CWL policy
                vpcFlowCwlPolicy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                    "Action": [
                        "logs:CreateLogGroup",
                        "logs:CreateLogStream",
                        "logs:PutLogEvents",
                        "logs:DescribeLogGroups",
                        "logs:DescribeLogStreams"
                    ],
                    "Effect": "Allow",
                    "Resource": logGroupArn + "*"
                    }
                ]
                }
                # create a VPC Flow logs Trust Policy  
                flowLogTrustPolicy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                    "Sid": "",
                    "Effect": "Allow",
                    "Principal": {
                        "Service": "vpc-flow-logs.amazonaws.com"
                    },
                    "Action": "sts:AssumeRole"
                    }
                ]
                }
                # create an IAM Policy 
                try:
                    response = iam.create_policy(
                        PolicyName='VPCFlowLogsPolicy-' + vpcId,
                        PolicyDocument=json.dumps(vpcFlowCwlPolicy),
                        Description='Allows ' + vpcId + ' to publish logs to CloudWatch'
                    )
                    cwlPolicyArn = str(response['Policy']['Arn'])
                except Exception as e:
                    print(e)
                    raise
                # create the VPC flow logs role
                try:
                    response = iam.create_role(
                        RoleName='VPCFlowLogsRole-' + vpcId,
                        AssumeRolePolicyDocument=json.dumps(flowLogTrustPolicy),
                        Description='Allows ' + vpcId + ' to publish logs to CloudWatch'
                    )
                    cwlRoleName = str(response['Role']['RoleName'])
                    cwlRoleArn = str(response['Role']['Arn'])
                except Exception as e:
                    print(e)
                    raise
                # attach the IAM policy to the new role
                try:
                    response = iam.attach_role_policy(RoleName=cwlRoleName,PolicyArn=cwlPolicyArn)
                    print(response)
                except Exception as e:
                    print(e)
                    raise
                # setup flow logging
                try:
                    response = ec2.create_flow_logs(
                        DryRun=False,
                        DeliverLogsPermissionArn=cwlRoleArn,
                        LogGroupName=logGroupName,
                        ResourceIds=[vpcId],
                        ResourceType='VPC',
                        TrafficType='REJECT',
                        LogDestinationType='cloud-watch-logs',
                        MaxAggregationInterval=60
                    )
                    print(response)
                    try:
                        response = securityhub.update_findings(
                            Filters={'Id': [{'Value': findingId,'Comparison': 'EQUALS'}]},
                            Note={'Text': 'A new CloudWatch logs group and IAM role was created for the VPC and flow logs are enabled and being sent to ' + logGroupName + ' and the finding was archived.','UpdatedBy': lambdaFunctionName},
                            RecordState='ARCHIVED'
                        )
                        print(response)
                    except Exception as e:
                        print(e)
                except Exception as e:
                    print(e)
                    raise
            else:
                logs = boto3.client('logs')
                iam = boto3.client('iam')
                ec2 = boto3.client('ec2')
                # create flow logs and wait for the creation to propogate
                try:
                    response = logs.create_log_group(logGroupName='VPCFlowLogs/' + vpcId)
                    print(response)
                    time.sleep(5)
                except Exception as e:
                    print(e)
                    raise
                # describe the created group and retrieve the ARN
                try:
                    response = logs.describe_log_groups(logGroupNamePrefix='VPCFlowLogs/' + vpcId)
                    logGroupArn = str(response['logGroups'][0]['arn'])
                    logGroupName = str(response['logGroups'][0]['logGroupName'])
                except Exception as e:
                    print(e)
                    raise
                # create a VPC Flow > CWL policy
                vpcFlowCwlPolicy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                    "Action": [
                        "logs:CreateLogGroup",
                        "logs:CreateLogStream",
                        "logs:PutLogEvents",
                        "logs:DescribeLogGroups",
                        "logs:DescribeLogStreams"
                    ],
                    "Effect": "Allow",
                    "Resource": logGroupArn + "*"
                    }
                ]
                }
                # create a VPC Flow logs Trust Policy  
                flowLogTrustPolicy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                    "Sid": "",
                    "Effect": "Allow",
                    "Principal": {
                        "Service": "vpc-flow-logs.amazonaws.com"
                    },
                    "Action": "sts:AssumeRole"
                    }
                ]
                }
                # create an IAM Policy 
                try:
                    response = iam.create_policy(
                        PolicyName='VPCFlowLogsPolicy-' + vpcId,
                        PolicyDocument=json.dumps(vpcFlowCwlPolicy),
                        Description='Allows ' + vpcId + ' to publish logs to CloudWatch'
                    )
                    cwlPolicyArn = str(response['Policy']['Arn'])
                except Exception as e:
                    print(e)
                    raise
                # create the VPC flow logs role
                try:
                    response = iam.create_role(
                        RoleName='VPCFlowLogsRole-' + vpcId,
                        AssumeRolePolicyDocument=json.dumps(flowLogTrustPolicy),
                        Description='Allows ' + vpcId + ' to publish logs to CloudWatch'
                    )
                    cwlRoleName = str(response['Role']['RoleName'])
                    cwlRoleArn = str(response['Role']['Arn'])
                except Exception as e:
                    print(e)
                    raise
                # attach the IAM policy to the new role
                try:
                    response = iam.attach_role_policy(RoleName=cwlRoleName,PolicyArn=cwlPolicyArn)
                    print(response)
                except Exception as e:
                    print(e)
                    raise
                # setup flow logging
                try:
                    response = ec2.create_flow_logs(
                        DryRun=False,
                        DeliverLogsPermissionArn=cwlRoleArn,
                        LogGroupName=logGroupName,
                        ResourceIds=[vpcId],
                        ResourceType='VPC',
                        TrafficType='REJECT',
                        LogDestinationType='cloud-watch-logs',
                        MaxAggregationInterval=60
                    )
                    print(response)
                    try:
                        response = securityhub.update_findings(
                            Filters={'Id': [{'Value': findingId,'Comparison': 'EQUALS'}]},
                            Note={'Text': 'A new CloudWatch logs group and IAM role was created for the VPC and flow logs are enabled and being sent to ' + logGroupName + ' and the finding was archived.','UpdatedBy': lambdaFunctionName},
                            RecordState='ARCHIVED'
                        )
                        print(response)
                    except Exception as e:
                        print(e)
                except Exception as e:
                    print(e)
                    raise