"""Deploy the Tripwire Arena to one free-tier EC2 instance, or tear it all down.

    python -m deploy.deploy_ec2            # create or redeploy, prints the URL
    python -m deploy.deploy_ec2 --destroy  # delete every resource this script made

What it creates (all tagged Project=tripwire, region $AWS_REGION or us-east-1):
- IAM role + instance profile: Bedrock Mantle inference, read two SSM parameters and the
  code bundle, and Session Manager access (no SSH keys, no port 22).
- SSM SecureString parameters: /tripwire/decider-api-key (from $DECIDER_API_KEY) and
  /tripwire/arena-password (generated once, also saved to deploy/.arena-password).
- A private S3 bucket holding `git archive HEAD` as code.tgz.
- A security group that opens port 80, and a t3.small (free-tier eligible) Amazon Linux 2023
  instance that runs `python -m web.server` under systemd.

The agent runs on Amazon Bedrock through the instance role; Decider checks go to the hosted
Decider API, because a 2B model on CPU needs more memory than a free-tier instance has.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import boto3

NAME = "tripwire-arena"
REGION = os.environ.get("AWS_REGION", "us-east-1")
INSTANCE_TYPE = os.environ.get("TRIPWIRE_INSTANCE_TYPE", "t3.small")
AMI = "resolve:ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
PARAM_KEY, PARAM_PW = "/tripwire/decider-api-key", "/tripwire/arena-password"
PW_FILE = Path(__file__).parent / ".arena-password"
ROOT = Path(__file__).resolve().parent.parent
TAGS = [{"Key": "Project", "Value": "tripwire"}, {"Key": "Name", "Value": NAME}]

session = boto3.Session(region_name=REGION)
iam, ssm, s3, ec2 = (session.client(x) for x in ("iam", "ssm", "s3", "ec2"))
ACCOUNT = session.client("sts").get_caller_identity()["Account"]
BUCKET = f"{NAME}-{ACCOUNT}-{REGION}"

USER_DATA = """#!/bin/bash
set -euxo pipefail
dnf install -y python3.11 python3.11-pip tar
mkdir -p /opt/tripwire
aws s3 cp s3://{bucket}/code.tgz /tmp/code.tgz --region {region}
tar xzf /tmp/code.tgz -C /opt/tripwire
python3.11 -m venv /opt/venv
/opt/venv/bin/pip install -q --upgrade pip
/opt/venv/bin/pip install -q -r /opt/tripwire/requirements-server.txt
cat > /opt/tripwire/run.sh <<'EOS'
#!/bin/bash
export AWS_REGION={region}
export DECIDER_API_KEY=$(aws ssm get-parameter --name {pkey} --with-decryption --query Parameter.Value --output text --region {region})
export ARENA_PASSWORD=$(aws ssm get-parameter --name {ppw} --with-decryption --query Parameter.Value --output text --region {region})
export HOST=0.0.0.0 PORT=80 PYTHONUNBUFFERED=1 PYTHONUTF8=1
cd /opt/tripwire && exec /opt/venv/bin/python -m web.server
EOS
chmod +x /opt/tripwire/run.sh
cat > /etc/systemd/system/tripwire.service <<'EOS'
[Unit]
Description=Tripwire Arena
After=network-online.target
[Service]
ExecStart=/opt/tripwire/run.sh
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
EOS
systemctl daemon-reload
systemctl enable --now tripwire
"""


def log(msg: str) -> None:
    print(f"[deploy] {msg}", flush=True)


# --------------------------------------------------------------------------- IAM
def ensure_role() -> str:
    trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"},
                                                     "Action": "sts:AssumeRole"}]}
    try:
        iam.create_role(RoleName=NAME, AssumeRolePolicyDocument=json.dumps(trust), Tags=TAGS[:1])
        log(f"created IAM role {NAME}")
    except iam.exceptions.EntityAlreadyExistsException:
        pass
    policy = {"Version": "2012-10-17", "Statement": [
        {"Sid": "BedrockMantleInference", "Effect": "Allow", "Resource": "*",
         "Action": ["bedrock-mantle:CreateInference", "bedrock-mantle:CallWithBearerToken",
                    "bedrock-mantle:Get*", "bedrock-mantle:List*", "bedrock:CallWithBearerToken"]},
        {"Sid": "TripwireParams", "Effect": "Allow", "Action": "ssm:GetParameter",
         "Resource": f"arn:aws:ssm:{REGION}:{ACCOUNT}:parameter/tripwire/*"},
        {"Sid": "CodeBundle", "Effect": "Allow", "Action": "s3:GetObject", "Resource": f"arn:aws:s3:::{BUCKET}/*"},
    ]}
    iam.put_role_policy(RoleName=NAME, PolicyName=f"{NAME}-inline", PolicyDocument=json.dumps(policy))
    iam.attach_role_policy(RoleName=NAME, PolicyArn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore")
    try:
        iam.create_instance_profile(InstanceProfileName=NAME)
        iam.add_role_to_instance_profile(InstanceProfileName=NAME, RoleName=NAME)
        log("created instance profile; waiting for IAM to propagate")
        time.sleep(15)
    except iam.exceptions.EntityAlreadyExistsException:
        pass
    return NAME


# --------------------------------------------------------------------------- config + code
def put_params() -> str:
    key = os.environ.get("DECIDER_API_KEY") or os.environ.get("STRANDS_DECIDER_API_KEY")
    if not key:
        sys.exit("DECIDER_API_KEY is not set: the deployed arena needs a hosted Decider API key.")
    ssm.put_parameter(Name=PARAM_KEY, Value=key, Type="SecureString", Overwrite=True)
    if PW_FILE.exists():
        pw = PW_FILE.read_text().strip()
    else:
        pw = secrets.token_urlsafe(9)
        PW_FILE.write_text(pw + "\n")
    ssm.put_parameter(Name=PARAM_PW, Value=pw, Type="SecureString", Overwrite=True)
    log(f"stored SSM parameters {PARAM_KEY} and {PARAM_PW} (password saved to {PW_FILE.relative_to(ROOT)})")
    return pw


def upload_code() -> None:
    try:
        s3.head_bucket(Bucket=BUCKET)
    except s3.exceptions.ClientError:
        kwargs = {} if REGION == "us-east-1" else {"CreateBucketConfiguration": {"LocationConstraint": REGION}}
        s3.create_bucket(Bucket=BUCKET, **kwargs)
        s3.put_public_access_block(Bucket=BUCKET, PublicAccessBlockConfiguration={
            "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True})
        s3.put_bucket_tagging(Bucket=BUCKET, Tagging={"TagSet": TAGS[:1]})
        log(f"created private bucket {BUCKET}")
    with tempfile.TemporaryDirectory() as tmp:
        tgz = Path(tmp) / "code.tgz"
        subprocess.run(["git", "archive", "--format=tar.gz", "-o", str(tgz), "HEAD"], cwd=ROOT, check=True)
        s3.upload_file(str(tgz), BUCKET, "code.tgz")
    log("uploaded `git archive HEAD` (commit first: uncommitted changes are not deployed)")


# --------------------------------------------------------------------------- network + instance
def ensure_sg() -> str:
    vpc = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"][0]["VpcId"]
    found = ec2.describe_security_groups(Filters=[{"Name": "group-name", "Values": [NAME]}, {"Name": "vpc-id", "Values": [vpc]}])
    if found["SecurityGroups"]:
        return found["SecurityGroups"][0]["GroupId"]
    sg = ec2.create_security_group(GroupName=NAME, Description="Tripwire Arena HTTP", VpcId=vpc,
                                   TagSpecifications=[{"ResourceType": "security-group", "Tags": TAGS}])["GroupId"]
    ec2.authorize_security_group_ingress(GroupId=sg, IpPermissions=[
        {"IpProtocol": "tcp", "FromPort": 80, "ToPort": 80, "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "arena"}]}])
    log(f"created security group {sg} (port 80 only)")
    return sg


def our_instances(states=("pending", "running", "stopping", "stopped")) -> list[str]:
    res = ec2.describe_instances(Filters=[{"Name": "tag:Project", "Values": ["tripwire"]},
                                          {"Name": "instance-state-name", "Values": list(states)}])
    return [i["InstanceId"] for r in res["Reservations"] for i in r["Instances"]]


def launch(sg: str) -> str:
    old = our_instances()
    if old:
        log(f"terminating previous instance(s) {old}")
        ec2.terminate_instances(InstanceIds=old)
        ec2.get_waiter("instance_terminated").wait(InstanceIds=old)
    for attempt in range(5):  # a fresh instance profile can take a little longer to be usable
        try:
            iid = ec2.run_instances(
                ImageId=AMI, InstanceType=INSTANCE_TYPE, MinCount=1, MaxCount=1,
                IamInstanceProfile={"Name": NAME}, SecurityGroupIds=[sg],
                UserData=USER_DATA.format(bucket=BUCKET, region=REGION, pkey=PARAM_KEY, ppw=PARAM_PW),
                MetadataOptions={"HttpTokens": "required"},
                TagSpecifications=[{"ResourceType": "instance", "Tags": TAGS}, {"ResourceType": "volume", "Tags": TAGS}],
            )["Instances"][0]["InstanceId"]
            break
        except ec2.exceptions.ClientError as exc:
            if "Invalid IAM Instance Profile" not in str(exc) or attempt == 4:
                raise
            time.sleep(10)
    log(f"launched {INSTANCE_TYPE} {iid}; waiting for it to run")
    ec2.get_waiter("instance_running").wait(InstanceIds=[iid])
    return iid


def wait_for_http(dns: str, timeout: int = 600) -> None:
    url = f"http://{dns}/"
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(url, timeout=5)
            return
        except urllib.error.HTTPError as exc:
            if exc.code == 401:  # up, and asking for the password
                return
        except Exception:
            pass
        time.sleep(10)
    log("the instance is running but the app did not answer yet; check it with Session Manager")


# --------------------------------------------------------------------------- destroy
def destroy() -> None:
    ids = our_instances(("pending", "running", "stopping", "stopped"))
    if ids:
        ec2.terminate_instances(InstanceIds=ids)
        log(f"terminating {ids}")
        ec2.get_waiter("instance_terminated").wait(InstanceIds=ids)
    for sg in ec2.describe_security_groups(Filters=[{"Name": "group-name", "Values": [NAME]}])["SecurityGroups"]:
        ec2.delete_security_group(GroupId=sg["GroupId"])
        log(f"deleted security group {sg['GroupId']}")
    try:
        iam.remove_role_from_instance_profile(InstanceProfileName=NAME, RoleName=NAME)
        iam.delete_instance_profile(InstanceProfileName=NAME)
    except iam.exceptions.NoSuchEntityException:
        pass
    try:
        iam.detach_role_policy(RoleName=NAME, PolicyArn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore")
        iam.delete_role_policy(RoleName=NAME, PolicyName=f"{NAME}-inline")
        iam.delete_role(RoleName=NAME)
        log(f"deleted IAM role {NAME}")
    except iam.exceptions.NoSuchEntityException:
        pass
    try:
        for obj in s3.list_objects_v2(Bucket=BUCKET).get("Contents", []):
            s3.delete_object(Bucket=BUCKET, Key=obj["Key"])
        s3.delete_bucket(Bucket=BUCKET)
        log(f"deleted bucket {BUCKET}")
    except s3.exceptions.ClientError:
        pass
    for name in (PARAM_KEY, PARAM_PW):
        try:
            ssm.delete_parameter(Name=name)
        except ssm.exceptions.ParameterNotFound:
            pass
    log("everything Tripwire created is gone")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--destroy", action="store_true")
    if ap.parse_args().destroy:
        return destroy()
    ensure_role()
    put_params()
    upload_code()
    iid = launch(ensure_sg())
    dns = ec2.describe_instances(InstanceIds=[iid])["Reservations"][0]["Instances"][0]["PublicDnsName"]
    log(f"installing the app on the instance (about 3-5 minutes): http://{dns}/")
    wait_for_http(dns)
    log(f"Tripwire Arena is live: http://{dns}/  (password in {PW_FILE.relative_to(ROOT)}, any username)")
    log("tear it all down after judging: python -m deploy.deploy_ec2 --destroy")


if __name__ == "__main__":
    main()
