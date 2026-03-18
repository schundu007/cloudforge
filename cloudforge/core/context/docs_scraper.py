"""
CloudForge @Docs Scraper
Auto-scrapes AWS, GCP, Azure, Terraform Registry, OTel spec,
and CIS Benchmarks into a vector store for agent context injection.

Mirrors Firebender's "up-to-date Android Knowledge" feature — continuously
scrapes latest SDKs, libraries, repos, and docs into context.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import structlog

log = structlog.get_logger()

DOCS_CACHE_DIR = Path(".cloudforge/docs-cache")
DOCS_CACHE_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class DocEntry:
    id: str
    source: str          # aws | gcp | azure | oci | terraform | otel | cis
    title: str
    url: str
    content: str
    fetched_at: str
    embedding: list[float] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


# ── Source registry ────────────────────────────────────────────────
DOC_SOURCES = [
    # AWS
    {
        "id": "aws-what-new",
        "source": "aws",
        "title": "AWS What's New",
        "url": "https://aws.amazon.com/about-aws/whats-new/recent/feed/",
        "tags": ["aws", "changelog"],
        "type": "rss",
    },
    {
        "id": "aws-iam-actions",
        "source": "aws",
        "title": "AWS IAM Actions Reference",
        "url": "https://docs.aws.amazon.com/service-authorization/latest/reference/reference_policies_actions-resources-contextkeys.html",
        "tags": ["aws", "iam", "security"],
        "type": "html",
    },
    {
        "id": "aws-checkov-checks",
        "source": "aws",
        "title": "Checkov AWS checks",
        "url": "https://raw.githubusercontent.com/bridgecrewio/checkov/main/docs/5.Policy%20Index/terraform.md",
        "tags": ["aws", "checkov", "security", "terraform"],
        "type": "markdown",
    },
    # Terraform Registry
    {
        "id": "tf-aws-provider",
        "source": "terraform",
        "title": "Terraform AWS Provider changelog",
        "url": "https://raw.githubusercontent.com/hashicorp/terraform-provider-aws/main/CHANGELOG.md",
        "tags": ["terraform", "aws", "changelog"],
        "type": "markdown",
    },
    {
        "id": "tf-google-provider",
        "source": "terraform",
        "title": "Terraform GCP Provider changelog",
        "url": "https://raw.githubusercontent.com/hashicorp/terraform-provider-google/main/CHANGELOG.md",
        "tags": ["terraform", "gcp", "changelog"],
        "type": "markdown",
    },
    {
        "id": "tf-azurerm-provider",
        "source": "terraform",
        "title": "Terraform AzureRM Provider changelog",
        "url": "https://raw.githubusercontent.com/hashicorp/terraform-provider-azurerm/main/CHANGELOG.md",
        "tags": ["terraform", "azure", "changelog"],
        "type": "markdown",
    },
    # GCP
    {
        "id": "gcp-release-notes",
        "source": "gcp",
        "title": "GCP Release Notes",
        "url": "https://cloud.google.com/feeds/gcp-release-notes.xml",
        "tags": ["gcp", "changelog"],
        "type": "rss",
    },
    # OTel
    {
        "id": "otel-collector-config",
        "source": "otel",
        "title": "OTel Collector Configuration",
        "url": "https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector/main/config/README.md",
        "tags": ["otel", "observability"],
        "type": "markdown",
    },
    {
        "id": "otel-semantic-conventions",
        "source": "otel",
        "title": "OTel Semantic Conventions",
        "url": "https://raw.githubusercontent.com/open-telemetry/semantic-conventions/main/README.md",
        "tags": ["otel", "observability"],
        "type": "markdown",
    },
    # CIS Benchmarks (public summaries)
    {
        "id": "cis-aws-foundations",
        "source": "cis",
        "title": "CIS AWS Foundations — Checkov mapping",
        "url": "https://raw.githubusercontent.com/bridgecrewio/checkov/main/docs/5.Policy%20Index/terraform_aws.md",
        "tags": ["cis", "aws", "security"],
        "type": "markdown",
    },
    # GitHub Actions
    {
        "id": "actions-security-hardening",
        "source": "github",
        "title": "GitHub Actions security hardening",
        "url": "https://raw.githubusercontent.com/nicowillis/github-actions-security/main/README.md",
        "tags": ["github-actions", "security"],
        "type": "markdown",
    },
]


class DocsScraper:
    """
    Scrapes doc sources on a schedule, chunks content, generates embeddings,
    and stores in a local vector cache for agent @Docs reference injection.
    """

    def __init__(self, cache_dir: Path = DOCS_CACHE_DIR, anthropic_api_key: str = ""):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.anthropic_api_key = anthropic_api_key
        self._client = httpx.AsyncClient(timeout=30, follow_redirects=True)

    async def refresh_all(self, force: bool = False) -> list[DocEntry]:
        """Refresh all doc sources. Skip if cached and fresh (< 6 hours old)."""
        entries: list[DocEntry] = []
        for source in DOC_SOURCES:
            try:
                entry = await self._fetch_source(source, force=force)
                if entry:
                    entries.append(entry)
                    log.info("docs.fetched", id=source["id"], chars=len(entry.content))
            except Exception as e:
                log.warning("docs.fetch_failed", id=source["id"], error=str(e))
        return entries

    async def search(self, query: str, tags: list[str] | None = None, top_k: int = 5) -> list[DocEntry]:
        """
        Search cached docs by query.
        Uses simple keyword matching — swap in a real vector store (Chroma/pgvector) for production.
        """
        all_entries = self._load_cache()
        if tags:
            all_entries = [e for e in all_entries if any(t in e.tags for t in tags)]

        # Score by keyword overlap
        q_words = set(query.lower().split())
        scored = []
        for entry in all_entries:
            text = (entry.title + " " + entry.content).lower()
            score = sum(1 for w in q_words if w in text)
            if score > 0:
                scored.append((score, entry))

        scored.sort(key=lambda x: -x[0])
        return [e for _, e in scored[:top_k]]

    def format_for_context(self, entries: list[DocEntry], max_chars: int = 8000) -> str:
        """Format doc entries for injection into agent system prompt."""
        parts = [f"## @Docs: {e.source.upper()} — {e.title}\nSource: {e.url}\n\n{e.content[:1000]}"
                 for e in entries]
        combined = "\n\n---\n\n".join(parts)
        return combined[:max_chars]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    async def _fetch_source(self, source: dict, force: bool = False) -> DocEntry | None:
        cache_file = self.cache_dir / f"{source['id']}.json"

        # Return cached if fresh
        if not force and cache_file.exists():
            age = time.time() - cache_file.stat().st_mtime
            if age < 6 * 3600:  # 6 hours
                data = json.loads(cache_file.read_text())
                return DocEntry(**data)

        resp = await self._client.get(source["url"])
        if resp.status_code != 200:
            log.warning("docs.http_error", id=source["id"], status=resp.status_code)
            return None

        raw = resp.text
        content = self._clean_content(raw, source["type"])

        entry = DocEntry(
            id=source["id"],
            source=source["source"],
            title=source["title"],
            url=source["url"],
            content=content[:20000],  # cap per entry
            fetched_at=datetime.utcnow().isoformat(),
            tags=source.get("tags", []),
        )

        cache_file.write_text(json.dumps(entry.__dict__))
        return entry

    def _clean_content(self, raw: str, content_type: str) -> str:
        if content_type == "rss":
            # Strip XML tags, keep text
            import re
            text = re.sub(r'<[^>]+>', ' ', raw)
            text = re.sub(r'\s+', ' ', text)
            return text.strip()[:15000]
        elif content_type == "html":
            import re
            text = re.sub(r'<script[^>]*>.*?</script>', '', raw, flags=re.DOTALL)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'\s+', ' ', text)
            return text.strip()[:15000]
        else:
            # Markdown / plain text — return as-is, trimmed
            return raw.strip()[:15000]

    def _load_cache(self) -> list[DocEntry]:
        entries = []
        for f in self.cache_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                entries.append(DocEntry(**data))
            except Exception:
                pass
        return entries

    async def close(self) -> None:
        await self._client.aclose()


# ── CLI ────────────────────────────────────────────────────────────
async def main() -> None:
    import asyncio
    scraper = DocsScraper()
    print("Refreshing all doc sources…")
    entries = await scraper.refresh_all(force=True)
    print(f"Fetched {len(entries)} sources")
    for e in entries:
        print(f"  {e.source:12} {e.title[:50]:50} {len(e.content):6} chars")
    await scraper.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
