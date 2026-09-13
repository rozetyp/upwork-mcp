"""Profile and connects tools for Upwork MCP.

These pages are Angular apps with no stable data-test attributes on their content,
so we parse the rendered innerText with structured regexes (verified against live pages
2026-09-13) rather than guessing CSS selectors that silently return nothing.
"""

import re
import asyncio

from ..browser.client import get_browser


async def _page_text(page, url, marker: str | None = None) -> str:
    """Navigate and return document.innerText once the Angular content has rendered.

    goto has already completed before we poll, so reading title/innerText here is safe
    (unlike polling mid-navigation). Wrapped defensively against the intermittent
    ERR_NETWORK_CHANGED blips seen on this connection.
    """
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except Exception:
        pass
    text = ""
    for _ in range(8):
        await asyncio.sleep(1.5)
        try:
            if "moment" in (await page.title()).lower():
                continue
        except Exception:
            continue
        try:
            text = await page.evaluate("() => document.body.innerText") or ""
        except Exception:
            text = ""
        if text and (not marker or marker in text):
            break
    return text


async def _resolve_profile_url(page) -> str | None:
    """Find the freelancer's own public profile URL (/freelancers/~...) from the settings
    page — derived at runtime so we never hardcode an identifying profile id."""
    await _page_text(page, "https://www.upwork.com/freelancers/settings/profile", marker="profile")
    try:
        href = await page.evaluate(
            "() => { const a = document.querySelector('a[href*=\"/freelancers/~\"]');"
            " return a ? a.getAttribute('href') : null; }"
        )
    except Exception:
        href = None
    if not href:
        return None
    return href if href.startswith("http") else "https://www.upwork.com" + href


def _parse_profile(text: str) -> dict:
    p = {}
    m = re.search(r"\n([^\n]+)\n\s*([^\n]+?) – [^\n]*local time", text)  # name / location line
    if m:
        p["name"] = m.group(1).strip()
        p["location"] = m.group(2).strip()
    m = re.search(r"local time\s*\n([^\n]+?)\s*\n\s*\$[\d,.]+\s*/\s*hr", text)
    if m:
        p["title"] = m.group(1).strip()
    m = re.search(r"\$[\d,.]+\s*/\s*hr", text)
    if m:
        p["hourly_rate"] = m.group(0).replace(" ", "")
    m = re.search(r"Connects:\s*([\d,]+)", text)
    if m:
        p["connects"] = int(m.group(1).replace(",", ""))
    m = re.search(r"Hours per week\s*\n\s*([^\n]+)", text)
    if m:
        p["hours_per_week"] = m.group(1).strip()
    ms = re.search(r"Skills\s*\n\s*Self-reported\s*\n(.*?)\n\s*Working style", text, re.S)
    if ms:
        p["skills"] = [s.strip() for s in ms.group(1).split("\n") if s.strip()]
    ml = re.search(r"Languages\s*\n(.*?)\n\s*(?:Verifications|Licenses|Education|Certifications|$)", text, re.S)
    if ml:
        langs = [l.strip() for l in ml.group(1).split("\n") if ":" in l]
        if langs:
            p["languages"] = langs
    return p


async def get_my_profile() -> dict:
    """Get your Upwork freelancer profile (name, title, hourly rate, location, skills, connects)."""
    browser = get_browser()
    await browser.ensure_logged_in()
    page = await browser.get_page()

    profile_url = await _resolve_profile_url(page)
    if not profile_url:
        return {"error": "Could not resolve profile URL from settings page"}

    # Wait for a late section ("Languages") so skills/languages have rendered, not just the header.
    text = await _page_text(page, profile_url, marker="Languages")
    profile = _parse_profile(text)
    profile["profile_url"] = profile_url
    return profile


async def get_connects_balance() -> dict:
    """Get current Upwork Connects balance and recent Connects activity."""
    browser = get_browser()
    await browser.ensure_logged_in()
    page = await browser.get_page()

    text = await _page_text(
        page, "https://www.upwork.com/nx/plans/connects/history", marker="balance"
    )

    connects = {}
    m = re.search(r"My balance\s*\n?\s*([\d,]+)\s+Connect", text, re.I)
    if m:
        connects["available"] = int(m.group(1).replace(",", ""))

    recent = []
    for m in re.finditer(r"(Applied to job|Added extra Connects|Refunded[^\n]*)\n([\s\S]*?)([+-]\d+)", text):
        action = m.group(1).strip()
        detail = re.sub(r"\s+", " ", m.group(2)).strip()
        entry = {"action": action, "connects": int(m.group(3))}
        if detail:
            entry["detail"] = detail
        recent.append(entry)
    if recent:
        connects["recent"] = recent[:10]

    return connects


async def get_profile_stats() -> dict:
    """Get profile statistics (job success, earnings, hours, jobs). These live on the public
    profile page; a brand-new freelancer with no work history returns an empty/near-empty set."""
    browser = get_browser()
    await browser.ensure_logged_in()
    page = await browser.get_page()

    profile_url = await _resolve_profile_url(page)
    if not profile_url:
        return {"error": "Could not resolve profile URL from settings page"}

    text = await _page_text(page, profile_url, marker="Skills")
    stats = {}
    m = re.search(r"(\d+)%\s*Job Success", text)
    if m:
        stats["job_success_score"] = m.group(1) + "%"
    m = re.search(r"(\$[\d,.]+[KMB]?\+?)\s*(?:total )?earned", text, re.I)
    if m:
        stats["total_earnings"] = m.group(1)
    m = re.search(r"([\d,]+)\s*(?:total )?hours\b", text, re.I)
    if m:
        stats["total_hours"] = m.group(1)
    m = re.search(r"(\d+)\s*(?:completed )?jobs?\b", text, re.I)
    if m:
        stats["jobs_completed"] = m.group(1)
    if re.search(r"Work history\s*\n\s*No items", text):
        stats["work_history"] = "none yet"
    return stats
