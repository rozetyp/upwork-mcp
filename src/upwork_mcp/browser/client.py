"""Browser client for Upwork automation using Patchright with CDP."""

import asyncio
import subprocess
import os
from pathlib import Path
from typing import Any
from patchright.async_api import async_playwright, Browser, BrowserContext, Page

PROFILE_DIR = Path.home() / ".upwork-mcp" / "chrome-profile"
CDP_PORT = 9222

# Real Chrome paths by platform
CHROME_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",  # macOS
    "/usr/bin/google-chrome",  # Linux
    "/usr/bin/chromium-browser",  # Linux Chromium
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",  # Windows
    "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",  # Windows x86
]


def find_chrome() -> str | None:
    """Find real Chrome/Chromium browser on system."""
    for path in CHROME_PATHS:
        if os.path.exists(path):
            return path
    return None


def is_chrome_running_with_debug() -> bool:
    """Check if Chrome is running with debug port."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def start_chrome_with_debug() -> bool:
    """Start Chrome with remote debugging enabled."""
    chrome_path = find_chrome()
    if not chrome_path:
        return False

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    # Start Chrome with debugging port
    subprocess.Popen(
        [
            chrome_path,
            f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={PROFILE_DIR}",
            "--no-first-run",
            "--no-default-browser-check",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait for Chrome to start
    for _ in range(10):
        if is_chrome_running_with_debug():
            return True
        asyncio.get_event_loop().run_until_complete(asyncio.sleep(0.5))

    return is_chrome_running_with_debug()


class UpworkBrowser:
    """Manages browser instance for Upwork automation via CDP."""

    def __init__(self, headless: bool = False, timeout: int = 30000):
        self.headless = headless  # Ignored for CDP mode
        self.timeout = timeout
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._started = False

    async def start(self) -> Page:
        """Connect to Chrome via CDP."""
        if self._started and self._page:
            return self._page

        # Ensure Chrome is running with debug port
        if not is_chrome_running_with_debug():
            print("Starting Chrome with debug port...")
            if not start_chrome_with_debug():
                raise RuntimeError(
                    f"Could not start Chrome. Please start it manually with:\n"
                    f'"{find_chrome()}" --remote-debugging-port={CDP_PORT}'
                )
            await asyncio.sleep(2)

        self._playwright = await async_playwright().start()

        # Connect via CDP
        self._browser = await self._playwright.chromium.connect_over_cdp(
            f"http://127.0.0.1:{CDP_PORT}"
        )

        contexts = self._browser.contexts
        if contexts:
            self._context = contexts[0]
            self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        else:
            self._context = await self._browser.new_context()
            self._page = await self._context.new_page()

        self._page.set_default_timeout(self.timeout)
        self._started = True
        return self._page

    async def get_page(self) -> Page:
        """Get or create page instance."""
        if not self._started or not self._page:
            return await self.start()
        return self._page

    async def close(self):
        """Disconnect from browser (doesn't close Chrome)."""
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._started = False

    # Cookies that only exist for an authenticated Upwork session.
    AUTH_COOKIES = {"master_access_token", "oauth2_global_js_token", "auth_session", "console_user"}

    async def is_logged_in(self) -> bool:
        """Check auth via session cookies first — fast, and no navigation.

        The old version navigated to a page and read the title, which false-negatived on
        Cloudflare's "just a moment" interstitial (reported "logged out" while logged in)
        and needlessly drove the browser. Auth cookies are the ground truth; only fall back
        to a light navigation check if none are present.
        """
        await self.get_page()  # ensure CDP context is connected
        try:
            cookies = await self._context.cookies()
            names = {c.get("name") for c in cookies if "upwork.com" in (c.get("domain") or "")}
            if names & self.AUTH_COOKIES:
                return True
        except Exception as e:
            print(f"Cookie check error: {e}")

        # Fallback: no auth cookie found — do a light check without a long wait loop.
        try:
            page = await self.get_page()
            await page.goto("https://www.upwork.com/nx/find-work/best-matches",
                            wait_until="domcontentloaded")
            for _ in range(6):
                await asyncio.sleep(1.5)
                if "moment" not in (await page.title()).lower():
                    break
            current_url = page.url.lower()
            return not ("login" in current_url or "ab/account-security" in current_url)
        except Exception as e:
            print(f"Login check error: {e}")
            return False

    async def ensure_logged_in(self) -> bool:
        """Verify login status, raise error if not logged in."""
        if not await self.is_logged_in():
            raise RuntimeError(
                "Not logged in to Upwork. Run 'uvx upwork-mcp --login' to authenticate."
            )
        return True

    async def navigate(self, url: str, wait_until: str = "domcontentloaded") -> Page:
        """Navigate to URL and return page."""
        page = await self.get_page()
        await page.goto(url, wait_until=wait_until)
        return page

    async def wait_for_selector(self, selector: str, timeout: int | None = None) -> Any:
        """Wait for element to appear."""
        page = await self.get_page()
        return await page.wait_for_selector(selector, timeout=timeout or self.timeout)

    async def extract_text(self, selector: str, default: str = "") -> str:
        """Extract text content from selector."""
        page = await self.get_page()
        try:
            element = await page.query_selector(selector)
            if element:
                return (await element.text_content() or "").strip()
        except Exception:
            pass
        return default

    async def extract_texts(self, selector: str) -> list[str]:
        """Extract text from all matching elements."""
        page = await self.get_page()
        elements = await page.query_selector_all(selector)
        texts = []
        for el in elements:
            text = await el.text_content()
            if text:
                texts.append(text.strip())
        return texts

    async def extract_attribute(self, selector: str, attribute: str, default: str = "") -> str:
        """Extract attribute value from selector."""
        page = await self.get_page()
        try:
            element = await page.query_selector(selector)
            if element:
                return (await element.get_attribute(attribute)) or default
        except Exception:
            pass
        return default


# Global browser instance
_browser: UpworkBrowser | None = None


def get_browser(headless: bool = False, timeout: int = 30000) -> UpworkBrowser:
    """Get or create global browser instance."""
    global _browser
    if _browser is None:
        _browser = UpworkBrowser(headless=headless, timeout=timeout)
    return _browser


async def close_browser():
    """Close global browser instance."""
    global _browser
    if _browser:
        await _browser.close()
        _browser = None
