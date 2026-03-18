"""
CloudForge MCP Server — Multi-Cloud
Exposes cloud operations as MCP tools:
  AWS:   discover resources, stream CloudWatch logs, list IAM
  GCP:   list GCP resources, stream Cloud Logging
  Azure: list resource groups, stream Azure Monitor
  OCI:   list compartments, stream OCI Logging
"""
from __future__ import annotations

import json
import os
from typing import Any

import boto3
import structlog
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

log = structlog.get_logger()
server = Server("cloudforge-cloud")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        # ── AWS ─────────────────────────────────────────────────────
        Tool(name="aws_list_resources",
             description="List AWS resources by type in a region",
             inputSchema={"type": "object", "properties": {
                 "resource_type": {"type": "string", "description": "e.g. ec2, s3, iam, lambda, rds, eks"},
                 "region": {"type": "string", "default": "us-east-1"},
             }, "required": ["resource_type"]}),

        Tool(name="aws_get_cloudwatch_logs",
             description="Stream recent CloudWatch log events from a log group",
             inputSchema={"type": "object", "properties": {
                 "log_group": {"type": "string"},
                 "minutes": {"type": "integer", "default": 15},
                 "filter_pattern": {"type": "string", "default": "ERROR"},
                 "region": {"type": "string", "default": "us-east-1"},
             }, "required": ["log_group"]}),

        Tool(name="aws_get_iam_policies",
             description="List attached IAM policies for a role or user",
             inputSchema={"type": "object", "properties": {
                 "principal_type": {"type": "string", "enum": ["role", "user"]},
                 "principal_name": {"type": "string"},
             }, "required": ["principal_type", "principal_name"]}),

        Tool(name="aws_describe_vpc",
             description="Describe VPC topology: subnets, route tables, security groups",
             inputSchema={"type": "object", "properties": {
                 "vpc_id": {"type": "string"},
                 "region": {"type": "string", "default": "us-east-1"},
             }, "required": ["vpc_id"]}),

        Tool(name="aws_get_cost_estimate",
             description="Get current month cost breakdown by service",
             inputSchema={"type": "object", "properties": {
                 "granularity": {"type": "string", "enum": ["DAILY", "MONTHLY"], "default": "MONTHLY"},
             }}),

        # ── GCP ─────────────────────────────────────────────────────
        Tool(name="gcp_list_resources",
             description="List GCP resources by type in a project",
             inputSchema={"type": "object", "properties": {
                 "project_id": {"type": "string"},
                 "resource_type": {"type": "string", "description": "e.g. compute.instances, storage.buckets"},
             }, "required": ["project_id", "resource_type"]}),

        Tool(name="gcp_get_logs",
             description="Query GCP Cloud Logging for recent entries",
             inputSchema={"type": "object", "properties": {
                 "project_id": {"type": "string"},
                 "filter": {"type": "string", "description": "Logging query filter"},
                 "minutes": {"type": "integer", "default": 15},
             }, "required": ["project_id"]}),

        # ── Azure ───────────────────────────────────────────────────
        Tool(name="azure_list_resources",
             description="List Azure resources in a resource group",
             inputSchema={"type": "object", "properties": {
                 "resource_group": {"type": "string"},
                 "subscription_id": {"type": "string"},
             }, "required": ["resource_group"]}),

        Tool(name="azure_get_logs",
             description="Query Azure Monitor for recent log entries",
             inputSchema={"type": "object", "properties": {
                 "workspace_id": {"type": "string"},
                 "query": {"type": "string"},
                 "minutes": {"type": "integer", "default": 15},
             }, "required": ["workspace_id", "query"]}),

        # ── Terraform ───────────────────────────────────────────────
        Tool(name="tf_state_resources",
             description="List all resources in current Terraform state",
             inputSchema={"type": "object", "properties": {
                 "state_file": {"type": "string", "description": "Path to terraform.tfstate"},
             }, "required": ["state_file"]}),

        Tool(name="tf_drift_check",
             description="Run terraform plan and return drift summary (no apply)",
             inputSchema={"type": "object", "properties": {
                 "module_path": {"type": "string"},
                 "use_localstack": {"type": "boolean", "default": False},
             }, "required": ["module_path"]}),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    try:
        result = await _dispatch(name, arguments)
        return [TextContent(type="text", text=json.dumps(result, default=str, indent=2))]
    except Exception as e:
        log.exception("mcp.cloud.error", tool=name, error=str(e))
        return [TextContent(type="text", text=f"Error: {e}")]


async def _dispatch(name: str, args: dict) -> Any:
    # ── AWS tools ──────────────────────────────────────────────────
    if name == "aws_list_resources":
        return await _aws_list_resources(args["resource_type"], args.get("region", "us-east-1"))

    elif name == "aws_get_cloudwatch_logs":
        return await _aws_get_cw_logs(
            args["log_group"],
            args.get("minutes", 15),
            args.get("filter_pattern", "ERROR"),
            args.get("region", "us-east-1"),
        )

    elif name == "aws_get_iam_policies":
        iam = boto3.client("iam")
        fn = iam.list_attached_role_policies if args["principal_type"] == "role" else iam.list_attached_user_policies
        kwarg = "RoleName" if args["principal_type"] == "role" else "UserName"
        resp = fn(**{kwarg: args["principal_name"]})
        return resp.get("AttachedPolicies", [])

    elif name == "aws_describe_vpc":
        ec2 = boto3.client("ec2", region_name=args.get("region", "us-east-1"))
        vpc_id = args["vpc_id"]
        subnets = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["Subnets"]
        sgs = ec2.describe_security_groups(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["SecurityGroups"]
        return {
            "vpc_id": vpc_id,
            "subnets": [{"id": s["SubnetId"], "cidr": s["CidrBlock"], "az": s["AvailabilityZone"]} for s in subnets],
            "security_groups": [{"id": sg["GroupId"], "name": sg["GroupName"]} for sg in sgs],
        }

    elif name == "aws_get_cost_estimate":
        ce = boto3.client("ce", region_name="us-east-1")
        import datetime
        end = datetime.date.today()
        start = end.replace(day=1)
        resp = ce.get_cost_and_usage(
            TimePeriod={"Start": str(start), "End": str(end)},
            Granularity=args.get("granularity", "MONTHLY"),
            Metrics=["UnblendedCost"],
            GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
        )
        return resp.get("ResultsByTime", [])

    # ── GCP tools ─────────────────────────────────────────────────
    elif name == "gcp_list_resources":
        try:
            from google.cloud import asset_v1
            client = asset_v1.AssetServiceClient()
            parent = f"projects/{args['project_id']}"
            assets = client.list_assets(request={"parent": parent,
                                                  "asset_types": [args["resource_type"]]})
            return [{"name": a.name, "type": a.asset_type} for a in assets]
        except ImportError:
            return {"error": "google-cloud-asset not installed"}

    elif name == "gcp_get_logs":
        try:
            from google.cloud import logging as gcp_logging
            import datetime
            client = gcp_logging.Client(project=args["project_id"])
            cutoff = datetime.datetime.utcnow() - datetime.timedelta(minutes=args.get("minutes", 15))
            entries = client.list_entries(
                filter_=f'{args.get("filter", "")} timestamp>="{cutoff.isoformat()}Z"',
                max_results=100,
            )
            return [{"timestamp": str(e.timestamp), "severity": str(e.severity),
                     "message": str(e.payload)} for e in entries]
        except ImportError:
            return {"error": "google-cloud-logging not installed"}

    # ── Azure tools ───────────────────────────────────────────────
    elif name == "azure_list_resources":
        try:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.resource import ResourceManagementClient
            cred = DefaultAzureCredential()
            sub_id = args.get("subscription_id") or os.getenv("AZURE_SUBSCRIPTION_ID", "")
            client = ResourceManagementClient(cred, sub_id)
            resources = client.resources.list_by_resource_group(args["resource_group"])
            return [{"name": r.name, "type": r.type, "location": r.location} for r in resources]
        except ImportError:
            return {"error": "azure-mgmt-resource not installed"}

    elif name == "azure_get_logs":
        try:
            from azure.monitor.query import LogsQueryClient, LogsQueryStatus
            from azure.identity import DefaultAzureCredential
            import datetime
            cred = DefaultAzureCredential()
            client = LogsQueryClient(cred)
            resp = client.query_workspace(
                workspace_id=args["workspace_id"],
                query=args["query"],
                timespan=datetime.timedelta(minutes=args.get("minutes", 15)),
            )
            if resp.status == LogsQueryStatus.SUCCESS:
                return [row for table in resp.tables for row in table.rows]
            return {"error": str(resp.partial_error)}
        except ImportError:
            return {"error": "azure-monitor-query not installed"}

    # ── Terraform tools ───────────────────────────────────────────
    elif name == "tf_state_resources":
        import json as _json
        from pathlib import Path
        state_file = Path(args["state_file"])
        if not state_file.exists():
            return {"error": f"State file not found: {state_file}"}
        state = _json.loads(state_file.read_text())
        resources = state.get("resources", [])
        return [{"type": r["type"], "name": r["name"], "provider": r.get("provider", "")} for r in resources]

    elif name == "tf_drift_check":
        import subprocess
        result = subprocess.run(
            ["terraform", "plan", "-detailed-exitcode", "-no-color"],
            cwd=args["module_path"],
            capture_output=True, text=True, timeout=120,
        )
        # exit code 0 = no changes, 1 = error, 2 = changes present
        return {
            "has_drift": result.returncode == 2,
            "error": result.returncode == 1,
            "output": result.stdout[-3000:],
            "stderr": result.stderr[-1000:],
        }

    return {"error": f"Unknown tool: {name}"}


async def _aws_list_resources(resource_type: str, region: str) -> list[dict]:
    service_map = {
        "ec2": lambda: boto3.client("ec2", region_name=region)
                       .describe_instances()["Reservations"],
        "s3": lambda: [{"Name": b["Name"]} for b in
                       boto3.client("s3").list_buckets().get("Buckets", [])],
        "iam": lambda: [{"RoleName": r["RoleName"], "Arn": r["Arn"]} for r in
                        boto3.client("iam").list_roles().get("Roles", [])],
        "lambda": lambda: [{"FunctionName": f["FunctionName"]} for f in
                           boto3.client("lambda", region_name=region).list_functions().get("Functions", [])],
        "rds": lambda: [{"DBInstanceIdentifier": d["DBInstanceIdentifier"]} for d in
                        boto3.client("rds", region_name=region).describe_db_instances().get("DBInstances", [])],
        "eks": lambda: {"clusters": boto3.client("eks", region_name=region).list_clusters().get("clusters", [])},
    }
    fn = service_map.get(resource_type.lower())
    if fn:
        return fn()
    return {"error": f"Unsupported resource type: {resource_type}"}


async def _aws_get_cw_logs(log_group: str, minutes: int, filter_pattern: str, region: str) -> list[dict]:
    import datetime, time
    cw = boto3.client("logs", region_name=region)
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - (minutes * 60 * 1000)
    resp = cw.filter_log_events(
        logGroupName=log_group,
        startTime=start_ms, endTime=end_ms,
        filterPattern=filter_pattern,
        limit=200,
    )
    return [{"timestamp": e["timestamp"], "message": e["message"]} for e in resp.get("events", [])]


async def main() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
