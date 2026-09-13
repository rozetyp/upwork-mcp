"""Job search and details tools for Upwork MCP."""

import re
import asyncio
import urllib.parse
from pydantic import BaseModel, Field
from ..browser.client import get_browser


class JobSearchParams(BaseModel):
    """Parameters for job search.

    Maps to Upwork's /nx/search/jobs/ query grammar. Every filter here is a real
    URL param the search endpoint honors — unlike the old model, nothing is dropped.
    """
    query: str = Field(description="Search keywords (supports AND/OR/NOT and \"quoted phrases\")")
    sort: str = Field(
        default="recency",
        description="Result order: 'recency' (newest first) or 'relevance'"
    )
    experience_level: str | None = Field(
        default=None,
        description="Experience level: entry, intermediate, or expert"
    )
    job_type: str | None = Field(
        default=None,
        description="Job type: 'hourly', 'fixed', or 'both'"
    )
    budget_min: int | None = Field(
        default=None, description="Minimum fixed-price budget (USD)"
    )
    budget_max: int | None = Field(
        default=None, description="Maximum fixed-price budget (USD)"
    )
    hourly_min: int | None = Field(
        default=None, description="Minimum hourly rate (USD/hr)"
    )
    payment_verified: bool = Field(
        default=False, description="Only clients with a verified payment method"
    )
    low_competition: bool = Field(
        default=False, description="Only jobs with <10 proposals so far (proposals=0-4,5-9)"
    )
    hired_before: bool = Field(
        default=False, description="Only clients who have hired at least once"
    )
    location: str | None = Field(
        default=None, description="Client location, e.g. 'United States'"
    )
    limit: int = Field(default=10, ge=1, le=50, description="Maximum number of results")


class JobDetailsParams(BaseModel):
    """Parameters for getting job details."""
    job_url: str = Field(description="Full Upwork job URL or job ID")


# ---- extraction helpers: one fast page.evaluate beats dozens of per-element awaits ----
_SEARCH_EXTRACT_JS = r"""
() => {
  const tiles = Array.from(document.querySelectorAll('article, [data-test="JobTile"]'));
  const out = [];
  for (const t of tiles) {
    const a = t.querySelector('[data-test="job-tile-title-link"], h2 a, h3 a, h4 a');
    if (!a) continue;
    const href = a.getAttribute('href') || '';
    if (!href.includes('/jobs/')) continue;
    out.push({
      title: (a.textContent || '').trim(),
      url: href.startsWith('http') ? href : 'https://www.upwork.com' + href,
      text: (t.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 600)
    });
  }
  return out;
}
"""

_DETAIL_JS = r"""
() => {
  const pick = (sels) => { for (const s of sels){ const e=document.querySelector(s); if(e && (e.innerText||'').trim()) return e.innerText.trim(); } return ''; };
  const title = pick(['h1','[data-test="job-title"]','[data-test="Title"]']);
  const desc = pick(['[data-test="Description"]','[data-test="job-description"]','.job-description','[data-test="Segmentations"]']);
  const feats = Array.from(document.querySelectorAll('[data-test="Features"] li, [data-test="BudgetAmount"], li')).map(e=>(e.innerText||'').replace(/\s+/g,' ').trim()).filter(Boolean);
  const skills = Array.from(document.querySelectorAll('[data-test="Skill"], [data-test="token"], a[href*="/nx/search/talent"]')).map(e=>(e.innerText||'').trim()).filter(x=>x&&x.length<40);
  const client = pick(['[data-test="AboutClientUser"]','[data-test="about-client-container"]','[data-test="ClientInfo"]','ul[data-test="ClientInfoList"]']);
  const connects = (document.body.innerText.match(/Send a proposal for:?\s*\d+ Connects|\d+ Connects/i)||[''])[0];
  return {title, desc: (desc||'').slice(0,4000), feats: [...new Set(feats)].filter(f=>!/^(Search|Home|Notifications|Opportunities|Contracts|Finances|Messages|Uma|Help)$/.test(f)).slice(0,12), skills:[...new Set(skills)].slice(0,20), client:(client||'').slice(0,800), connects};
}
"""


def _parse_tile_fields(text: str) -> dict:
    """Parse a search tile's innerText. Budget/type live BEFORE 'Proposals';
    client-spend lives after — split so they don't cross-contaminate (the old bug)."""
    f = {}
    head = re.split(r"Proposals", text, flags=re.I)[0]
    m = re.search(r"(Hourly|Fixed[- ]price)", head, re.I)
    if m:
        f["type"] = m.group(1).replace("Fixed-price", "Fixed price")
    rng = re.search(r"\$[\d,]+(?:\.\d+)?\s*[-–]\s*\$[\d,]+(?:\.\d+)?", head)
    if rng:
        f["budget"] = rng.group(0)
    else:
        dm = re.findall(r"\$[\d,]+(?:\.\d+)?", head)
        if dm:
            f["budget"] = dm[-1]
    m = re.search(r"Proposals?:?\s*(Less than \d+|\d+ to \d+|\d+\+|\d+)", text, re.I)
    if m:
        f["proposals"] = m.group(1)  # competition signal — the key filter
    m = re.search(r"(\d+\s+(?:minute|hour|day|week|month)s?\s+ago|yesterday|last week)", text, re.I)
    if m:
        f["posted"] = m.group(1)
    if "Payment verified" in text or "Payment method verified" in text:
        f["payment_verified"] = True
    m = re.search(r"\$[\d,.KMB]+\+?\s*(?:total )?spent", text, re.I)
    if m:
        f["client_spent"] = m.group(0)
    return f


async def search_jobs(params: JobSearchParams) -> list[dict]:
    """Search for jobs on Upwork matching the specified criteria.

    Returns a list of job summaries with title, budget, competition (proposals), and URL.
    """
    browser = get_browser()
    page = await browser.get_page()

    # Build search URL — /nx/search/jobs/ is the real keyword search;
    # /nx/find-work/best-matches ignores ?q= and returns the personalized home feed.
    base_url = "https://www.upwork.com/nx/search/jobs/"
    query_params = {"q": params.query}

    # Sort: recency (newest) or relevance
    query_params["sort"] = "recency" if params.sort.lower() == "recency" else "relevance"

    # Job type: t=0 hourly, t=1 fixed, both -> t=0,1
    if params.job_type:
        jt = params.job_type.lower()
        query_params["t"] = {"hourly": "0", "fixed": "1", "both": "0,1"}.get(jt, "1")

    # Experience level -> contractor_tier
    if params.experience_level:
        level_map = {"entry": "1", "intermediate": "2", "expert": "3"}
        level = level_map.get(params.experience_level.lower())
        if level:
            query_params["contractor_tier"] = level

    # Fixed-price budget range -> amount=min-max
    if params.budget_min is not None or params.budget_max is not None:
        lo = params.budget_min if params.budget_min is not None else 0
        hi = params.budget_max if params.budget_max is not None else ""
        query_params["amount"] = f"{lo}-{hi}"

    # Minimum hourly rate -> hourly_rate=min-
    if params.hourly_min is not None:
        query_params["hourly_rate"] = f"{params.hourly_min}-"

    # Verified-payment clients only
    if params.payment_verified:
        query_params["payment_verified"] = "1"

    # Low competition: fewer than 10 proposals so far
    if params.low_competition:
        query_params["proposals"] = "0-4,5-9"

    # Clients who have hired at least once
    if params.hired_before:
        query_params["client_hires"] = "1-9,10-"

    # Client location filter
    if params.location:
        query_params["location"] = params.location

    url = f"{base_url}?{urllib.parse.urlencode(query_params)}"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except Exception:
        pass

    # Wait for tiles to render (or Cloudflare "just a moment" to clear) without a fixed sleep.
    for _ in range(8):
        await asyncio.sleep(1.5)
        if "moment" in (await page.title()).lower():
            continue
        count = await page.evaluate(
            "() => document.querySelectorAll('article,[data-test=\"JobTile\"]').length"
        )
        if count > 0:
            break

    raw = await page.evaluate(_SEARCH_EXTRACT_JS)

    jobs, seen = [], set()
    for r in raw:
        title = (r.get("title") or "").strip()
        job_url = r.get("url") or ""
        if not title or job_url in seen:
            continue
        seen.add(job_url)
        job = {"title": title, "url": job_url}
        job.update(_parse_tile_fields(r.get("text") or ""))
        jobs.append(job)
        if len(jobs) >= params.limit:
            break

    return jobs


async def get_job_details(params: JobDetailsParams) -> dict:
    """Get detailed information about a specific Upwork job posting.

    Returns comprehensive job details including description, client history,
    skills required, and application requirements.
    """
    browser = get_browser()
    page = await browser.get_page()

    # Normalize URL
    url = params.job_url
    if not url.startswith("http"):
        url = f"https://www.upwork.com/jobs/{url}"

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except Exception:
        pass
    for _ in range(8):
        await asyncio.sleep(1.5)
        if "moment" not in (await page.title()).lower():
            break

    job = {"url": url}
    try:
        d = await page.evaluate(_DETAIL_JS)
    except Exception:
        d = {}

    if d.get("title"):
        job["title"] = d["title"]
    if d.get("desc"):
        job["description"] = d["desc"]
    if d.get("feats"):
        job["features"] = d["feats"]          # type / budget / experience / duration
    if d.get("skills"):
        job["skills"] = d["skills"]
    if d.get("client"):
        job["client"] = d["client"]           # full client history block (rating, spend, hires)
    if d.get("connects"):
        m = re.search(r"\d+", d["connects"])
        if m:
            job["connects_required"] = int(m.group(0))

    return job
