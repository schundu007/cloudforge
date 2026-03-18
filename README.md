# CloudForge

> The cloud infrastructure-native coding agent — modeled on Firebender's architecture for cloud infra.

Writes GitHub Actions pipelines, Terraform IaC, security policies (IAM/OPA/Checkov), and observability configs (OTel/Prometheus/Grafana) for **AWS, GCP, Azure, and OCI**. Tests in the emulator, auto-fixes failures, presents a reviewable git diff.

## Interfaces

| Interface | Usage |
|-----------|-------|
| **VS Code Extension** | ⌘K fast-edit HCL/YAML, ⌘L agent chat panel, SSE streaming diff review |
| **CLI** | `cloudforge run "add OIDC auth"` — Rich streaming output, interactive accept/reject |
| **GitHub App** | PR comments `/cloudforge generate ...`, label-driven hooks, auto-PR on pass |
| **Web Dashboard** | FastAPI + SSE real-time run logs, diff review UI |

## Quickstart

```bash
# 1. Clone and install
git clone https://github.com/your-org/cloudforge
cd cloudforge
pip install uv
uv sync

# 2. Set env vars
export ANTHROPIC_API_KEY=sk-ant-...
export GITHUB_TOKEN=ghp_...

# 3. Start the local stack
docker compose up -d   # LocalStack + Redis + Prometheus + Grafana

# 4. Run the agent
cloudforge run "add a private subnet with NAT gateway to the networking module"

# 5. Or use the API
uvicorn cloudforge.interfaces.web.api.main:app --reload
curl -X POST http://localhost:8000/runs \
  -H "Content-Type: application/json" \
  -d '{"intent": "add OIDC auth to deploy pipeline", "repo_root": "/path/to/repo"}'
```

## Architecture

```
Intent
  └── Orchestrator (Claude Sonnet + MCP)
        ├── Context Store (repo tree, tf state, GHA workflows)
        ├── Pipeline Writer    → GitHub Actions YAML  → act dry-run
        ├── IaC Writer         → Terraform/CDK HCL    → tf plan (LocalStack) + checkov + tfsec
        ├── Security Writer    → IAM / OPA / Rego      → opa test
        └── Observability Writer → OTel / Prometheus / Grafana → pint
              └── Auto-Fix Loop (max 5 retries)
                    └── Git Diff → Accept / Reject → GitHub PR
```

## VS Code Extension

Install from the marketplace or:
```bash
cd interfaces/vscode-ext
npm install && npm run package
code --install-extension cloudforge-0.1.0.vsix
```

Key bindings:
- `⌘K` — Fast Edit: select HCL/YAML block, describe the change
- `⌘L` — Open agent chat
- `⌘⇧S` — Scan and fix current file

## Hooks

Configure in `cloudforge-hooks.yaml`:

```yaml
hooks:
  pre-commit:
    enabled: true
    block_on: [HIGH, CRITICAL]
  pr-opened:
    enabled: true
    tf_plan_comment: true
  cost-threshold:
    enabled: true
    monthly_delta_usd: 500
  drift:
    enabled: true
    schedule: "0 */6 * * *"
  log-error:
    enabled: true
    sources: [cloudwatch, gcp_logs, azure_monitor]
```

## Security

- Every generated resource block is scanned by checkov + tfsec **before** the diff is shown
- IAM policies are automatically least-privilege (no wildcards without justification)
- Secrets are scrubbed from context before calling the Claude API
- tf state is never sent to the API — only resource type counts and module names
- All pipelines use OIDC; static credentials are rejected

## Naming Convention

All generated resources follow: `[prefix]-[project]-[suffix][env]-[random]-[resource]`

Example: `tf-payments-svcprod-x7k-sg`

## Cloud Platform Support

| Platform | Auth | Emulator | Checkov rules |
|----------|------|----------|---------------|
| AWS | OIDC (aws-actions/configure-aws-credentials@v4) | LocalStack | CKV_AWS_* |
| GCP | WIF (google-github-actions/auth@v2) | GCP emulator | CKV_GCP_* |
| Azure | OIDC (azure/login@v2) | Azurite | CKV_AZURE_* |
| OCI | OIDC (oracle-actions) | — | CKV_OCI_* |
