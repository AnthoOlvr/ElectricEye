#This file is part of ElectricEye.
#SPDX-License-Identifier: Apache-2.0

#Licensed to the Apache Software Foundation (ASF) under one
#or more contributor license agreements.  See the NOTICE file
#distributed with this work for additional information
#regarding copyright ownership.  The ASF licenses this file
#to you under the Apache License, Version 2.0 (the
#"License"); you may not use this file except in compliance
#with the License.  You may obtain a copy of the License at

#http://www.apache.org/licenses/LICENSE-2.0

#Unless required by applicable law or agreed to in writing,
#software distributed under the License is distributed on an
#"AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
#KIND, either express or implied.  See the License for the
#specific language governing permissions and limitations
#under the License.

import boto3
import nmap3
import datetime
from check_register import CheckRegister
from dateutil.parser import parse

registry = CheckRegister()
# Boto3 clients
ec2 = boto3.client("ec2")
elbv2 = boto3.client("elbv2")
# Instantiate a NMAP scanner for TCP scans to define ports
nmap = nmap3.NmapScanTechniques()

def ec2_paginate(cache):
    instanceList = []
    response = cache.get("instances")
    if response:
        return response
    paginator = ec2.get_paginator("describe_instances")
    if paginator:
        for page in paginator.paginate(Filters=[{'Name': 'instance-state-name','Values': ['running']}]):
            for r in page["Reservations"]:
                for i in r["Instances"]:
                    instanceList.append(i)
        cache["instances"] = instanceList
        return cache["instances"]

def describe_load_balancers(cache):
    # loop through ELBv2 load balancers
    response = cache.get("describe_load_balancers")
    if response:
        return response
    cache["describe_load_balancers"] = elbv2.describe_load_balancers()
    return cache["describe_load_balancers"]

def scan_host(host_ip, instance_id):
    # This function carries out the scanning of EC2 instances using TCP without service fingerprinting
    # runs Top 10 (minus HTTPS) as well as various DB/Cache/Docker/K8s/NFS/SIEM ports
    try:
        results = nmap.nmap_tcp_scan(
            host_ip,
            # FTP, SSH, TelNet, SMTP, HTTP, POP3, NetBIOS, SMB, RDP, MSSQL, MySQL/MariaDB, NFS, Docker, Oracle, PostgreSQL, 
            # Kibana, VMWare, Proxy, Splunk, K8s, Redis, Kafka, Mongo, Rabbit/AmazonMQ, SparkUI
            args="-Pn -p 21,22,23,25,80,110,139,445,3389,1433,3306,2049,2375,1521,5432,5601,8182,8080,8089,10250,6379,9092,27017,5672,4040"
        )

        print(f"Scanning EC2 instance {instance_id} on {host_ip}")
        return results
    except KeyError:
        results = None

@registry.register_check("ec2")
def ec2_attack_surface_open_tcp_port_check(cache: dict, awsAccountId: str, awsRegion: str, awsPartition: str) -> dict:
    """[AttackSurface.EC2.{checkIdNumber}] EC2 Instances should not be publicly reachable on {serviceName}"""
    # ISO Time
    iso8601Time = (datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc).isoformat())
    # Paginate the iterator object from Cache
    for i in ec2_paginate(cache=cache):
        instanceId = str(i["InstanceId"])
        instanceArn = (f"arn:{awsPartition}:ec2:{awsRegion}:{awsAccountId}:instance/{instanceId}")
        instanceType = str(i["InstanceType"])
        instanceImage = str(i["ImageId"])
        subnetId = str(i["SubnetId"])
        vpcId = str(i["VpcId"])
        try:
            instanceLaunchedAt = str(i["BlockDeviceMappings"][0]["Ebs"]["AttachTime"])
        except KeyError:
            instanceLaunchedAt = str(i["LaunchTime"])
        # If Public DNS or Public IP are empty it means the instance is not public, we can skip this
        try:
            hostIp = i["PublicIpAddress"]
            if hostIp == ("" or None):
                continue
        except KeyError:
            continue
        else:
            scanner = scan_host(hostIp, instanceId)
            # NoneType returned on KeyError due to Nmap errors
            if scanner == None:
                continue
            else:
                # Loop the results of the scan - starting with Open Ports which require a combination of
                # a Public Instance, an open SG rule, and a running service/server on the host itself
                # use enumerate and a fixed offset to product the Check Title ID number
                for index, p in enumerate(scanner[hostIp]["ports"]):
                    # Parse out the Protocol, Port, Service, and State/State Reason from NMAP Results
                    checkIdNumber = str(int(index + 1))
                    portNumber = int(p["portid"])
                    if portNumber == 8089:
                        serviceName = 'SPLUNKD'
                    elif portNumber == 10250:
                        serviceName = 'KUBERNETES-API'
                    elif portNumber == 5672:
                        serviceName = 'RABBITMQ'
                    elif portNumber == 4040:
                        serviceName = 'SPARK-WEBUI'
                    else:
                        serviceName = str(p["service"]["name"]).upper()
                    serviceStateReason = str(p["reason"])
                    serviceState = str(p["state"])
                    # This is a failing check
                    if serviceState == "open":
                        finding = {
                            "SchemaVersion": "2018-10-08",
                            "Id": f"{instanceArn}/attack-surface-ec2-open-{serviceName}-check",
                            "ProductArn": f"arn:{awsPartition}:securityhub:{awsRegion}:{awsAccountId}:product/{awsAccountId}/default",
                            "GeneratorId": instanceArn,
                            "AwsAccountId": awsAccountId,
                            "Types": [
                                "Software and Configuration Checks/AWS Security Best Practices/Network Reachability",
                                "TTPs/Discovery"
                            ],
                            "FirstObservedAt": iso8601Time,
                            "CreatedAt": iso8601Time,
                            "UpdatedAt": iso8601Time,
                            "Severity": {"Label": "HIGH"},
                            "Confidence": 99,
                            "Title": f"[AttackSurface.EC2.{checkIdNumber}] EC2 Instances should not be publicly reachable on {serviceName}",
                            "Description": f"EC2 instance {instanceId} is publicly reachable on port {portNumber} which corresponds to the {serviceName} service. When Services are successfully fingerprinted by the ElectricEye Attack Surface Management Auditor it means the instance is Public, has an open Secuirty Group rule, and a running service on the host which adversaries can also see. Refer to the remediation insturctions for an example of a way to secure EC2 instances.",
                            "Remediation": {
                                "Recommendation": {
                                    "Text": "EC2 Instances should only have the minimum necessary ports open to achieve their purposes, allow traffic from authorized sources, and use other defense-in-depth and hardening strategies. For a basic view on traffic authorization into your instances refer to the Authorize inbound traffic for your Linux instances section of the Amazon Elastic Compute Cloud User Guide",
                                    "Url": "https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/authorizing-access-to-an-instance.html"
                                }
                            },
                            "ProductFields": {"Product Name": "ElectricEye"},
                            "Resources": [
                                {
                                    "Type": "AwsEc2Instance",
                                    "Id": instanceArn,
                                    "Partition": awsPartition,
                                    "Region": awsRegion,
                                    "Details": {
                                        "AwsEc2Instance": {
                                            "Type": instanceType,
                                            "ImageId": instanceImage,
                                            "VpcId": vpcId,
                                            "SubnetId": subnetId,
                                            "LaunchedAt": parse(instanceLaunchedAt).isoformat()
                                        }
                                    },
                                }
                            ],
                            "Compliance": {
                                "Status": "FAILED",
                                "RelatedRequirements": [
                                    "NIST CSF PR.AC-3",
                                    "NIST SP 800-53 AC-1",
                                    "NIST SP 800-53 AC-17",
                                    "NIST SP 800-53 AC-19",
                                    "NIST SP 800-53 AC-20",
                                    "NIST SP 800-53 SC-15",
                                    "AICPA TSC CC6.6",
                                    "ISO 27001:2013 A.6.2.1",
                                    "ISO 27001:2013 A.6.2.2",
                                    "ISO 27001:2013 A.11.2.6",
                                    "ISO 27001:2013 A.13.1.1",
                                    "ISO 27001:2013 A.13.2.1"
                                ]
                            },
                            "Workflow": {"Status": "NEW"},
                            "RecordState": "ACTIVE"
                        }
                        yield finding
                    else:
                        finding = {
                            "SchemaVersion": "2018-10-08",
                            "Id": f"{instanceArn}/attack-surface-ec2-open-{serviceName}-check",
                            "ProductArn": f"arn:{awsPartition}:securityhub:{awsRegion}:{awsAccountId}:product/{awsAccountId}/default",
                            "GeneratorId": instanceArn,
                            "AwsAccountId": awsAccountId,
                            "Types": [
                                "Software and Configuration Checks/AWS Security Best Practices/Network Reachability",
                                "TTPs/Discovery"
                            ],
                            "FirstObservedAt": iso8601Time,
                            "CreatedAt": iso8601Time,
                            "UpdatedAt": iso8601Time,
                            "Severity": {"Label": "INFORMATIONAL"},
                            "Confidence": 99,
                            "Title": f"[AttackSurface.EC2.{checkIdNumber}] EC2 Instances should not be publicly reachable on {serviceName}",
                            "Description": f"EC2 instance {instanceId} is not publicly reachable on port {portNumber} which corresponds to the {serviceName} service due to {serviceStateReason}. Instances and their respective Security Groups should still be reviewed for minimum necessary access.",
                            "Remediation": {
                                "Recommendation": {
                                    "Text": "EC2 Instances should only have the minimum necessary ports open to achieve their purposes, allow traffic from authorized sources, and use other defense-in-depth and hardening strategies. For a basic view on traffic authorization into your instances refer to the Authorize inbound traffic for your Linux instances section of the Amazon Elastic Compute Cloud User Guide",
                                    "Url": "https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/authorizing-access-to-an-instance.html"
                                }
                            },
                            "ProductFields": {"Product Name": "ElectricEye"},
                            "Resources": [
                                {
                                    "Type": "AwsEc2Instance",
                                    "Id": instanceArn,
                                    "Partition": awsPartition,
                                    "Region": awsRegion,
                                    "Details": {
                                        "AwsEc2Instance": {
                                            "Type": instanceType,
                                            "ImageId": instanceImage,
                                            "VpcId": vpcId,
                                            "SubnetId": subnetId,
                                            "LaunchedAt": parse(instanceLaunchedAt).isoformat()
                                        }
                                    },
                                }
                            ],
                            "Compliance": {
                                "Status": "PASSED",
                                "RelatedRequirements": [
                                    "NIST CSF PR.AC-3",
                                    "NIST SP 800-53 AC-1",
                                    "NIST SP 800-53 AC-17",
                                    "NIST SP 800-53 AC-19",
                                    "NIST SP 800-53 AC-20",
                                    "NIST SP 800-53 SC-15",
                                    "AICPA TSC CC6.6",
                                    "ISO 27001:2013 A.6.2.1",
                                    "ISO 27001:2013 A.6.2.2",
                                    "ISO 27001:2013 A.11.2.6",
                                    "ISO 27001:2013 A.13.1.1",
                                    "ISO 27001:2013 A.13.2.1"
                                ]
                            },
                            "Workflow": {"Status": "RESOLVED"},
                            "RecordState": "ARCHIVED"
                        }
                        yield finding

@registry.register_check("elbv2")
def elbv2_attack_surface_open_tcp_port_check(cache: dict, awsAccountId: str, awsRegion: str, awsPartition: str) -> dict:
    """[AttackSurface.ELBv2.{checkIdNumber}] Application Load Balancers should not be publicly reachable on {serviceName}"""
    # ISO Time
    iso8601Time = (datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc).isoformat())
    # Loop ELBs and select the public ALBs
    for lb in describe_load_balancers(cache)["LoadBalancers"]:
        elbv2Arn = str(lb["LoadBalancerArn"])
        elbv2Name = str(lb["LoadBalancerName"])
        elbv2DnsName = str(lb["DNSName"])
        elbv2LbType = str(lb["Type"])
        elbv2Scheme = str(lb["Scheme"])
        elbv2VpcId = str(lb["VpcId"])
        elbv2IpAddressType = str(lb["IpAddressType"])
        if (elbv2Scheme == 'internet-facing' and elbv2LbType == 'application'):
            scanner = scan_host(elbv2DnsName, elbv2Name)
            # NoneType returned on KeyError due to Nmap errors
            if scanner == None:
                continue
            else:
                # Pull out the IP resolution of the DNS Name
                keys = scanner.keys()
                hostIp = (list(keys)[0])
                # Loop the results of the scan - starting with Open Ports which require a combination of
                # a Public Instance, an open SG rule, and a running service/server on the host itself
                # use enumerate and a fixed offset to product the Check Title ID number
                for index, p in enumerate(scanner[hostIp]["ports"]):
                    # Parse out the Protocol, Port, Service, and State/State Reason from NMAP Results
                    checkIdNumber = str(int(index + 1))
                    portNumber = int(p["portid"])
                    if portNumber == 8089:
                        serviceName = 'SPLUNKD'
                    elif portNumber == 10250:
                        serviceName = 'KUBERNETES-API'
                    elif portNumber == 5672:
                        serviceName = 'RABBITMQ'
                    elif portNumber == 4040:
                        serviceName = 'SPARK-WEBUI'
                    else:
                        serviceName = str(p["service"]["name"]).upper()
                    serviceStateReason = str(p["reason"])
                    serviceState = str(p["state"])
                    # This is a failing check
                    if serviceState == "open":
                        finding = {
                            "SchemaVersion": "2018-10-08",
                            "Id": f"{elbv2Arn}/attack-surface-elbv2-open-{serviceName}-check",
                            "ProductArn": f"arn:{awsPartition}:securityhub:{awsRegion}:{awsAccountId}:product/{awsAccountId}/default",
                            "GeneratorId": elbv2Arn,
                            "AwsAccountId": awsAccountId,
                            "Types": [
                                "Software and Configuration Checks/AWS Security Best Practices/Network Reachability",
                                "TTPs/Discovery"
                            ],
                            "FirstObservedAt": iso8601Time,
                            "CreatedAt": iso8601Time,
                            "UpdatedAt": iso8601Time,
                            "Severity": {"Label": "HIGH"},
                            "Confidence": 99,
                            "Title": f"[AttackSurface.ELBv2.{checkIdNumber}] Application Load Balancers should not be publicly reachable on {serviceName}",
                            "Description": f"Application load balancer {elbv2Name} is publicly reachable on port {portNumber} which corresponds to the {serviceName} service. When Services are successfully fingerprinted by the ElectricEye Attack Surface Management Auditor it means the instance is Public, has an open Secuirty Group rule, and a running service on the host which adversaries can also see. Refer to the remediation insturctions for an example of a way to secure EC2 instances.",
                            "Remediation": {
                                "Recommendation": {
                                    "Text": "For more information on ALB security group reccomendations refer to the Security groups for your Application Load Balancer section of the Application Load Balancers User Guide.",
                                    "Url": "https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-update-security-groups.html#security-group-recommended-rules"
                                }
                            },
                            "ProductFields": {"Product Name": "ElectricEye"},
                            "Resources": [
                                {
                                    "Type": "AwsElbv2LoadBalancer",
                                    "Id": elbv2Arn,
                                    "Partition": awsPartition,
                                    "Region": awsRegion,
                                    "Details": {
                                        "AwsElbv2LoadBalancer": {
                                            "DNSName": elbv2DnsName,
                                            "IpAddressType": elbv2IpAddressType,
                                            "Scheme": elbv2Scheme,
                                            "Type": elbv2LbType,
                                            "VpcId": elbv2VpcId
                                        }
                                    }
                                }
                            ],
                            "Compliance": {
                                "Status": "FAILED",
                                "RelatedRequirements": [
                                    "NIST CSF PR.AC-3",
                                    "NIST SP 800-53 AC-1",
                                    "NIST SP 800-53 AC-17",
                                    "NIST SP 800-53 AC-19",
                                    "NIST SP 800-53 AC-20",
                                    "NIST SP 800-53 SC-15",
                                    "AICPA TSC CC6.6",
                                    "ISO 27001:2013 A.6.2.1",
                                    "ISO 27001:2013 A.6.2.2",
                                    "ISO 27001:2013 A.11.2.6",
                                    "ISO 27001:2013 A.13.1.1",
                                    "ISO 27001:2013 A.13.2.1"
                                ]
                            },
                            "Workflow": {"Status": "NEW"},
                            "RecordState": "ACTIVE"
                        }
                        yield finding
                    else:
                        finding = {
                            "SchemaVersion": "2018-10-08",
                            "Id": f"{elbv2Arn}/attack-surface-elbv2-open-{serviceName}-check",
                            "ProductArn": f"arn:{awsPartition}:securityhub:{awsRegion}:{awsAccountId}:product/{awsAccountId}/default",
                            "GeneratorId": elbv2Arn,
                            "AwsAccountId": awsAccountId,
                            "Types": [
                                "Software and Configuration Checks/AWS Security Best Practices/Network Reachability",
                                "TTPs/Discovery"
                            ],
                            "FirstObservedAt": iso8601Time,
                            "CreatedAt": iso8601Time,
                            "UpdatedAt": iso8601Time,
                            "Severity": {"Label": "INFORMATIONAL"},
                            "Confidence": 99,
                            "Title": f"[AttackSurface.ELBv2.{checkIdNumber}] Application Load Balancers should not be publicly reachable on {serviceName}",
                            "Description": f"Application load balancer {elbv2Name} is not publicly reachable on port {portNumber} which corresponds to the {serviceName} service due to {serviceStateReason}. ALBs and their respective Security Groups should still be reviewed for minimum necessary access.",
                            "Remediation": {
                                "Recommendation": {
                                    "Text": "For more information on ALB security group reccomendations refer to the Security groups for your Application Load Balancer section of the Application Load Balancers User Guide.",
                                    "Url": "https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-update-security-groups.html#security-group-recommended-rules"
                                }
                            },
                            "ProductFields": {"Product Name": "ElectricEye"},
                            "Resources": [
                                {
                                    "Type": "AwsElbv2LoadBalancer",
                                    "Id": elbv2Arn,
                                    "Partition": awsPartition,
                                    "Region": awsRegion,
                                    "Details": {
                                        "AwsElbv2LoadBalancer": {
                                            "DNSName": elbv2DnsName,
                                            "IpAddressType": elbv2IpAddressType,
                                            "Scheme": elbv2Scheme,
                                            "Type": elbv2LbType,
                                            "VpcId": elbv2VpcId
                                        }
                                    }
                                }
                            ],
                            "Compliance": {
                                "Status": "PASSED",
                                "RelatedRequirements": [
                                    "NIST CSF PR.AC-3",
                                    "NIST SP 800-53 AC-1",
                                    "NIST SP 800-53 AC-17",
                                    "NIST SP 800-53 AC-19",
                                    "NIST SP 800-53 AC-20",
                                    "NIST SP 800-53 SC-15",
                                    "AICPA TSC CC6.6",
                                    "ISO 27001:2013 A.6.2.1",
                                    "ISO 27001:2013 A.6.2.2",
                                    "ISO 27001:2013 A.11.2.6",
                                    "ISO 27001:2013 A.13.1.1",
                                    "ISO 27001:2013 A.13.2.1"
                                ]
                            },
                            "Workflow": {"Status": "RESOLVED"},
                            "RecordState": "ARCHIVED"
                        }
                        yield finding
        else:
            continue