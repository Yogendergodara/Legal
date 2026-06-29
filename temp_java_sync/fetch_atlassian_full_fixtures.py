#!/usr/bin/env python3
"""Fetch full Atlassian legal documents and build vendor-matched E2E fixtures."""

from __future__ import annotations

import json
import re
import sys
from html import unescape
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"

# Official Atlassian legal pages (matches user paste, August 2026 era)
DOCUMENTS: list[tuple[str, str, str]] = [
    ("atlassian-privacy-policy", "Atlassian Privacy Policy", "https://www.atlassian.com/legal/privacy-policy"),
    (
        "atlassian-copyright-trademark",
        "Reporting Copyright and Trademark Violations",
        "https://www.atlassian.com/legal/copyright-and-trademark-violations",
    ),
    (
        "atlassian-third-party-code-policy",
        "Atlassian Third-Party Code Policy",
        "https://www.atlassian.com/legal/third-party-code-policy",
    ),
    (
        "atlassian-advisory-services-policy",
        "Atlassian Advisory Services Policy",
        "https://www.atlassian.com/legal/advisory-services-policy",
    ),
    (
        "atlassian-acceptable-use-policy",
        "Atlassian Acceptable Use Policy",
        "https://www.atlassian.com/legal/acceptable-use-policy",
    ),
    (
        "atlassian-government-amendment",
        "Atlassian Government Amendment",
        "https://www.atlassian.com/legal/government-amendment",
    ),
    (
        "atlassian-data-processing-addendum",
        "Atlassian Data Processing Addendum",
        "https://www.atlassian.com/legal/data-processing-addendum",
    ),
    (
        "atlassian-product-specific-terms",
        "Atlassian Product-Specific Terms",
        "https://www.atlassian.com/legal/product-terms",
    ),
    ("atlassian-ai-terms", "Atlassian AI Terms", "https://www.atlassian.com/legal/ai-terms"),
    (
        "atlassian-customer-agreement",
        "Atlassian Customer Agreement",
        "https://www.atlassian.com/legal/atlassian-customer-agreement",
    ),
]


def html_to_text(html: str) -> str:
    """Rough HTML → plain text for legal pages."""
    html = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
    html = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", html)
    html = re.sub(r"(?is)<(br|/p|/div|/h[1-6]|/li|/tr)[^>]*>", "\n", html)
    html = re.sub(r"(?is)<[^>]+>", " ", html)
    text = unescape(html)
    text = text.replace("\xa0", " ")
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    # Drop nav/footer noise: keep from first "Effective starting" or numbered section
    start = 0
    for i, ln in enumerate(lines):
        if re.search(r"Effective starting|^\d+\.\s", ln, re.I):
            start = i
            break
    body = "\n\n".join(lines[start:])
    return body.strip()


def fetch_text(client: httpx.Client, url: str) -> str:
    r = client.get(url, follow_redirects=True, timeout=120.0)
    r.raise_for_status()
    text = html_to_text(r.text)
    if len(text) < 500:
        raise RuntimeError(f"Extracted text too short from {url} ({len(text)} chars)")
    return text


def main() -> int:
    FIXTURES.mkdir(exist_ok=True)
    policies: list[dict[str, str]] = []
    contract_text = ""

    with httpx.Client(
        headers={"User-Agent": "LegalReviewHarness/1.0 (vendor-matched E2E test)"}
    ) as client:
        for ref, title, url in DOCUMENTS:
            print(f"Fetching {title} ...", flush=True)
            text = fetch_text(client, url)
            path = FIXTURES / f"{ref}.txt"
            path.write_text(text, encoding="utf-8")
            print(f"  -> {path.name}: {len(text):,} chars", flush=True)
            if ref == "atlassian-customer-agreement":
                contract_text = text
                (FIXTURES / "atlassian_customer_agreement.txt").write_text(text, encoding="utf-8")
            else:
                policies.append(
                    {
                        "policy_ref": ref,
                        "title": title,
                        "text": text,
                        "policy_type": "saas",
                    }
                )

    e2e = {
        "tenant_id": "e2e-demo",
        "policies": policies,
        "contract_file": "atlassian_customer_agreement.txt",
        "contract_chars": len(contract_text),
        "policy_count": len(policies),
        "total_policy_chars": sum(len(p["text"]) for p in policies),
    }
    (FIXTURES / "atlassian_e2e.json").write_text(
        json.dumps(e2e, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(
        f"\nWrote {len(policies)} policies + contract "
        f"({e2e['total_policy_chars']:,} policy chars, {len(contract_text):,} contract chars)",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
