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

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from playwright_stealth import Stealth

BASE_URL  = "https://learnedleague.com"
LOGIN_URL = f"{BASE_URL}/ucp.php?mode=login"


class LearnedLeagueSession:
    def __init__(self, headless: bool = True):
        """
        headless=True  -> invisible browser (normal use)
        headless=False -> visible browser window (useful for debugging)
        """
        self.headless  = headless
        self.logged_in = False

        self._pw      = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless)
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
    def get_html(self, url: str) -> str:
        """Navigate to a URL and return the page HTML."""
        self._require_login()
        self._page.goto(url, wait_until="networkidle", timeout=30_000)
        return self._page.content()

    def get_soup(self, url: str, parser: str = "lxml") -> BeautifulSoup:
        """Navigate to a URL and return a BeautifulSoup object."""
        return BeautifulSoup(self.get_html(url), parser)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _require_login(self):
        if not self.logged_in:
            raise RuntimeError("Not logged in. Call .login(username, password) first.")
