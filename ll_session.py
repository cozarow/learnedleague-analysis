"""
ll_session.py
-------------
Authenticated access to LearnedLeague using Playwright for both login
and all subsequent page fetches. Keeps a single persistent browser
context so cookies are maintained across all requests.

Usage:
    from ll_session import LearnedLeagueSession

    ll = LearnedLeagueSession()
    ll.login("your_username", "your_password")

    soup = ll.get_soup("https://learnedleague.com/match.php?108&1&A_Nebula")
    ll.close()  # call when done

    # Or use as a context manager:
    with LearnedLeagueSession() as ll:
        ll.login("your_username", "your_password")
        soup = ll.get_soup("https://learnedleague.com/match.php?108&1&A_Nebula")

Requirements:
    pip install playwright beautifulsoup4 lxml
    python -m playwright install chromium
"""

import random
import time

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, Error as PWError, TimeoutError as PWTimeout
from playwright_stealth import Stealth

BASE_URL  = "https://learnedleague.com"
LOGIN_URL = f"{BASE_URL}/ucp.php?mode=login"


class RateLimitedError(Exception):
    """Raised when the site appears to be blocking or rate-limiting us."""


class LearnedLeagueSession:
    def __init__(self, headless: bool = False, offscreen: bool = True):
        """
        headless=True   -> invisible browser. NOTE: learnedleague.com's
                            Cloudflare protection currently 403s headless
                            Chromium outright, so this will fail to log in.
                            Kept as an option in case that changes.
        headless=False  -> real (headed) browser window, which Cloudflare
                            allows through. This is required for login to
                            work right now.
        offscreen=True  -> (only matters when headless=False) positions the
                            browser window off-screen so it doesn't steal
                            focus or interrupt whatever else you're doing.
                            Set to False if you want to watch it for
                            debugging.
        """
        self.headless  = headless
        self.logged_in = False

        launch_args = []
        if not headless and offscreen:
            launch_args.append("--window-position=-32000,-32000")

        self._pw      = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless, args=launch_args)
        self._context = self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        self._page = self._context.new_page()
        Stealth().apply_stealth_sync(self._page)

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        """Shut down the browser cleanly."""
        try:
            self._browser.close()
            self._pw.stop()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------
    def login(self, username: str, password: str) -> bool:
        """
        Log in to LearnedLeague. The browser session (and its cookies)
        persists for all subsequent get_soup / get_html calls.
        """
        print("Launching browser for login...")
        self._page.goto(LOGIN_URL, wait_until="networkidle", timeout=30_000)

        try:
            self._page.fill('input[name="username"]', username)
            self._page.fill('input[name="password"]', password)
            self._page.click('input[type="submit"]')
            self._page.wait_for_load_state("networkidle", timeout=20_000)
        except PWTimeout:
            raise RuntimeError(
                "Timed out waiting for login. Try headless=False to debug."
            )

        html = self._page.content()
        if "mode=logout" not in html and "Log out" not in html:
            raise RuntimeError(
                "Login failed -- check your username and password.\n"
                "Tip: call LearnedLeagueSession(headless=False) to watch the browser."
            )

        self.logged_in = True
        print(f"Logged in as '{username}'")
        return True

    # ------------------------------------------------------------------
    # Fetching (reuses the same authenticated browser context)
    # ------------------------------------------------------------------
    def get_html(self, url: str, max_retries: int = 3, validate=None) -> str:
        """
        Navigate to a URL and return the page HTML.

        If the request comes back with an error status, fails outright, or
        fails the optional `validate(html) -> bool` check, that's treated as
        a possible rate limit / soft block: wait ~5 minutes and retry, up
        to max_retries times, before raising RateLimitedError.

        `validate` matters because a block isn't always an HTTP error status
        -- learnedleague.com has been observed serving a normal-looking
        200 OK page that's actually a soft rate-limit/interstitial page
        instead of real content. Without a content check, that looks like
        success and silently produces bad data instead of retrying.
        """
        self._require_login()

        for attempt in range(max_retries + 1):
            try:
                resp = self._page.goto(url, wait_until="networkidle", timeout=30_000)
                status = resp.status if resp else None
            except PWError:
                status = None

            reason = None
            if status is None or status >= 400:
                reason = f"status={status}"
            else:
                html = self._page.content()
                if validate is None or validate(html):
                    return html
                reason = "response didn't look like the expected page (possible soft block)"

            if attempt == max_retries:
                raise RateLimitedError(f"Giving up on {url} after {max_retries} retries ({reason})")

            wait_s = random.uniform(4.5 * 60, 5.5 * 60)
            print(
                f"  [possible rate limit] {reason} for {url} -- "
                f"sleeping {wait_s / 60:.1f} min before retry {attempt + 1}/{max_retries}"
            )
            remaining = wait_s
            while remaining > 0:
                chunk = min(60, remaining)
                time.sleep(chunk)
                remaining -= chunk
                if remaining > 0:
                    print(f"    ... still waiting ({remaining / 60:.1f} min left)")

    def get_soup(self, url: str, parser: str = "lxml", validate=None) -> BeautifulSoup:
        """
        Navigate to a URL and return a BeautifulSoup object.

        `validate`, if given, is a function (BeautifulSoup) -> bool checking
        that the page looks like what was expected; see get_html.
        """
        html_validate = None
        if validate is not None:
            html_validate = lambda html: validate(BeautifulSoup(html, parser))
        return BeautifulSoup(self.get_html(url, validate=html_validate), parser)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _require_login(self):
        if not self.logged_in:
            raise RuntimeError("Not logged in. Call .login(username, password) first.")
