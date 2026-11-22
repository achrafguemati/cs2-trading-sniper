---
name: github-open-source-audit
description: Audits open-source CS2 and Steam Market tools for reusable code, architecture patterns, licenses, and security risks.
---

# GitHub Open-Source Audit Skill

This skill governs the systematic discovery, inspection, and auditing of public GitHub repositories relevant to Counter-Strike 2 (CS2) market data, Steam Community Market (SCM) protocols, listing detection, inventory tracking, price history, and trading analytics.

## Core Objective

Identify high-quality, legally compliant, and architecturally safe components (parsers, data models, algorithms, schemas) for reuse or reference, while strictly eliminating code with security risks, authentication exploits, or incompatible licenses.

---

## 1. Audit Principles

1. **Source Code Inspection Over Descriptions:** Never evaluate a repository solely by its README, description, or GitHub stars. Inspect actual source files, imports, network layers, and test suites.
2. **Recent Activity Priority:** Prioritize repositories updated within the last 12 months. Retain older repositories only if they contain unique, technically verified protocol reverse-engineering or stable schemas.
3. **Strict Safety & Anti-Bot Screening:** Automatically reject any repository containing:
   - Automated market purchasing, listing creation, or trade offer execution.
   - Session hijacking, `steamLoginSecure` extraction, or two-factor secret storage (`maFile`).
   - Proxy rotation, CAPTCHA bypass, or Cloudflare/Akamai evasion scripts.
4. **License Integrity:**
   - Permissive licenses (MIT, Apache-2.0, BSD-2/3-Clause) may be considered for code reuse, provided attribution notices are preserved.
   - Reciprocal / copyleft licenses (GPL, AGPL) or unlicensed repositories must be classified as **Reference Only** (ideas/concepts only) or **Reject**; zero verbatim code may be copied.
   - Proprietary or non-commercial licenses are strictly rejected.

---

## 2. Audit Workflow

For each candidate repository, follow these discrete audit steps:

### Step 1: Metadata & Health Capture
Record:
- Full Repository URL and primary branch.
- SPDX License identifier (verified against the `LICENSE` file, not just GitHub metadata).
- Latest commit date and last release date (UTC).
- Primary language, framework, and key dependencies.
- Maintenance status (Active, Maintained, Stale, Abandoned).

### Step 2: Architecture & Implementation Analysis
Inspect the codebase to determine:
- What domain problem does the repository solve?
- Does it use modern Steam protocols (e.g. 2026 SSR React hydration, `valve-ssr-data`, `G...` grouped URLs) or legacy endpoints (`/render`, raw scraping)?
- How does it handle rate limiting, backoff, and network errors?
- How are monetary values represented (integer minor units vs floating-point)?
- How are items identified (classid/instanceid, item_nameid, market_hash_name)?

### Step 3: Safety & Constraint Verification
Verify adherence to project engineering standards:
- Is network communication strictly read-only?
- Are credentials or sensitive cookies requested or parsed?
- Does it attempt to evade rate limits or rotate proxies?

### Step 4: Final Classification
Assign exactly one classification status:
- **Reuse Candidate:** Permissively licensed, clean implementation, strictly read-only, integer financial precision, directly solves a project module (e.g. data schemas or mapping tables).
- **Reference Only:** Useful architecture, regex, or protocol documentation, but cannot be copied due to license constraints (e.g. GPL/AGPL), language mismatch, or partial architectural incompatibility.
- **Reject:** Fails technical quality standards, uses deprecated endpoints, or does not provide value.
- **License or Security Concern:** Contains automated trade execution, credential extraction, bot evasion, or unknown/hostile licensing.

Document the exact technical reason and specific file evidence supporting the classification.

---

## 3. Deliverable Format

All audit findings must be recorded in `docs/research/open-source-project-watchlist.md` following the standardized audit entry template.
