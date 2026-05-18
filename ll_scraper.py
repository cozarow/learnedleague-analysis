"""
ll_scraper.py
-------------
Example: scrape your LearnedLeague profile and recent match results.

Run:
    python ll_scraper.py

You'll be prompted for your username and password (or hard-code them below).
"""

import getpass
from ll_session import LearnedLeagueSession

# ── Config ────────────────────────────────────────────────────────────
# Option A: enter interactively (safer)
USERNAME = input("LL username: ").strip()
PASSWORD = getpass.getpass("LL password: ")

# Option B: hard-code (convenient, but don't commit to git)
# USERNAME = "your_username"
# PASSWORD = "your_password"
# ──────────────────────────────────────────────────────────────────────


def scrape_profile(ll: LearnedLeagueSession, username: str, pid: int):
    """Fetch and print basic profile stats."""
    url  = f"https://learnedleague.com/profiles.php?{pid}"
    soup = ll.get_soup(url)

    print(f"\n=== Profile: {username} ===")

    # Page title
    title = soup.find("title")
    if title:
        print(f"Page title : {title.text.strip()}")

    # Find stat tables — structure varies by season; this grabs all tables
    tables = soup.find_all("table")
    print(f"Tables found: {len(tables)}")

    for i, table in enumerate(tables[:3]):   # preview first 3
        rows = table.find_all("tr")
        print(f"\n  Table {i + 1} ({len(rows)} rows):")
        for row in rows[:5]:                 # preview first 5 rows
            cells = [td.get_text(strip=True) for td in row.find_all(["th", "td"])]
            print("    ", " | ".join(cells))


def scrape_matchday(ll: LearnedLeagueSession, season: int, matchday: int):
    """
    Fetch results for a specific season + matchday.
    Adjust the URL pattern to match whatever page you need.
    """
    url  = f"https://learnedleague.com/match.php?{season}&{matchday}"
    soup = ll.get_soup(url)

    print(f"\n=== Season {season}, Matchday {matchday} ===")
    tables = soup.find_all("table")
    print(f"Tables found: {len(tables)}")

    for i, table in enumerate(tables[:2]):
        rows = table.find_all("tr")
        print(f"\n  Table {i + 1} ({len(rows)} rows):")
        for row in rows[:8]:
            cells = [td.get_text(strip=True) for td in row.find_all(["th", "td"])]
            print("    ", " | ".join(cells))


if __name__ == "__main__":
    ll = LearnedLeagueSession()
    ll.login(USERNAME, PASSWORD)

    # Scrape your own profile
    scrape_profile(ll, USERNAME)

    # Scrape a matchday (edit season/matchday numbers as needed)
    # scrape_matchday(ll, season=95, matchday=1)

    print("\n✓ Done. Extend this script to extract whatever data you need!")
