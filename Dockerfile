# CloudForge — Multi-stage Dockerfile
# Stage 1: builder with all dev deps
FROM python:3.12-slim AS builder

WORKDIR /app

# Install UV
RUN pip install uv

# Copy dependency files
COPY pyproject.toml .
COPY README.md .

# Copy cloudforge source for installation
COPY cloudforge cloudforge

# Install dependencies into a virtual environment
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --upgrade pip && \
    /opt/venv/bin/pip install --no-cache-dir .

# Stage 2: runtime — security tools + CloudForge
FROM python:3.12-slim AS runtime

WORKDIR /app

# System deps: git (worktrees), curl, unzip
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Install Terraform / OpenTofu
RUN ARCH=$(dpkg --print-architecture) && \
    curl -fsSL "https://releases.hashicorp.com/terraform/1.9.8/terraform_1.9.8_linux_${ARCH}.zip" \
    -o terraform.zip && unzip terraform.zip && mv terraform /usr/local/bin/ && rm terraform.zip

# Install checkov
RUN pip install checkov --no-cache-dir

# Install tfsec
RUN ARCH=$(dpkg --print-architecture) && \
    curl -fsSL "https://github.com/aquasecurity/tfsec/releases/latest/download/tfsec-linux-${ARCH}" \
    -o /usr/local/bin/tfsec && chmod +x /usr/local/bin/tfsec

# Install OPA
RUN ARCH=$(dpkg --print-architecture) && \
    curl -fsSL "https://github.com/open-policy-agent/opa/releases/latest/download/opa_linux_${ARCH}_static" \
    -o /usr/local/bin/opa && chmod +x /usr/local/bin/opa

# Install actionlint (optional - skip if download fails)
RUN ARCH=$(dpkg --print-architecture) && \
    VERSION=$(curl -s https://api.github.com/repos/rhysd/actionlint/releases/latest | grep tag_name | cut -d'"' -f4 || echo "v1.7.7") && \
    (curl -fsSL "https://github.com/rhysd/actionlint/releases/download/${VERSION}/actionlint_${VERSION#v}_linux_${ARCH}.tar.gz" \
    | tar xz -C /usr/local/bin actionlint) || echo "actionlint install skipped"

# Install pint (PromQL linter)
RUN pip install pint-PromQL --no-cache-dir || true

# Copy virtualenv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy source
COPY . .

# Install the package
RUN pip install -e . --no-deps --no-cache-dir

# Create non-root user
RUN useradd -m -u 1000 cloudforge && \
    chown -R cloudforge:cloudforge /app
USER cloudforge

# Git config for background agent commits
RUN git config --global user.name "CloudForge" && \
    git config --global user.email "agent@cloudforge.dev"

EXPOSE 8000
EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/health || exit 1

# Shell form so $PORT (injected by Railway/Heroku-style platforms) expands.
# Single worker: run state lives in an in-memory dict, so a second worker would
# serve 404s for runs created by the first.
CMD uvicorn cloudforge.interfaces.web.api.main:app \
    --host 0.0.0.0 --port ${PORT:-8000} --workers 1
