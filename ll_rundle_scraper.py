"""
ll_rundle_scraper.py
-------------
Scrape LearnedLeague question-answer data for all players in a rundle.

Usage:
    python ll_rundle_scraper.py

Returns a dict mapping abbreviated player names to a list of 150 elements:
    1   = answered correctly
    0   = answered incorrectly
    "F" = forfeited that match day

Example output:
    {
        "SchE": [1, 1, 0, 1, 0, 1, 0, 1, ...],  # 150 elements
        "KeJ4": [0, 1, 1, 1, 1, 0, "F", "F", ...],
        ...
    }
"""

import getpass
import time
from ll_session import LearnedLeagueSession

# ── Config ─────────────────────────────────────────────────────────────────────
USERNAME = input("LL username: ").strip()
PASSWORD = getpass.getpass("LL password: ")

SEASON      = 108          # e.g. 108 for LL108
NUM_DAYS    = 25           # match days per season
NUM_QUESTIONS = 6          # questions per match day
RUNDLE_NAME = "A_Nebula"  # as it appears in the URL, e.g. "A_Nebula", "B_Andromeda"

DELAY = 0.5  # seconds between requests — be polite to the server
# ───────────────────────────────────────────────────────────────────────────────

BASE_URL = "https://learnedleague.com"


def parse_matchday(soup, match_day: int) -> dict:
    """
    Parse one match.php page and return a dict of:
        { player_abbrev: [q1, q2, q3, q4, q5, q6] }
    where each value is 1, 0, or "F".

    The standings table has columns Q1-Q6 (classes c0/c1/cF) followed
    by rank, player name, and stat columns.
    """
    CLASS_MAP = {"c1": 1, "c0": 0, "cF": "F"}

    results = {}
    table = soup.find("table", class_="std")
    if table is None:
        print(f"  Warning: no standings table found for match day {match_day}")
        return results

    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 8:
            continue  # header or spacer row

        # First 6 cells are Q1-Q6 answer cells
        q_cells = cells[:6]
        answers = []
        is_player_row = True
        for td in q_cells:
            cls = td.get("class", [])
            # class is a list e.g. ['c1'] or ['c0'] or ['cF']
            matched = None
            for c in cls:
                if c in CLASS_MAP:
                    matched = CLASS_MAP[c]
                    break
            if matched is None:
                is_player_row = False
                break
            answers.append(matched)

        if not is_player_row:
            continue

        # Player name is in the cell with class 'std-midleft'
        name_cell = row.find("td", class_="std-midleft")
        if name_cell is None:
            continue

        # The abbreviated name is the text node after the <a> tag
        # e.g. "&nbsp;SchE" -> "SchE"
        name_text = name_cell.get_text(strip=True)
        # get_text collapses the img/flag, leaving just the abbrev
        player = name_text.strip()

        if player:
            results[player] = answers

    return results


def scrape_rundle(
    ll: LearnedLeagueSession,
    season: int,
    rundle_name: str,
    num_days: int = NUM_DAYS,
    num_questions: int = NUM_QUESTIONS,
) -> dict:
    """
    Scrape all match days for a rundle and return:
        { player_abbrev: [150 elements of 1/0/"F"] }
    """
    # Initialise empty lists for each player (discovered on first match day)
    all_data = {}

    for day in range(1, num_days + 1):
        url = f"{BASE_URL}/match.php?{season}&{day}&{rundle_name}"
        print(f"Fetching match day {day:>2}/{num_days}  {url}")

        soup = ll.get_soup(url)
        day_results = parse_matchday(soup, day)

        if not day_results:
            print(f"  No data found for day {day} — skipping.")
            # Fill with None so indices stay aligned
            for player in all_data:
                all_data[player].extend([None] * num_questions)
        else:
            for player, answers in day_results.items():
                if player not in all_data:
                    # New player seen for first time — backfill prior days with None
                    all_data[player] = [None] * (day - 1) * num_questions
                all_data[player].extend(answers)

        time.sleep(DELAY)

    # Sanity check
    expected = num_days * num_questions
    for player, answers in all_data.items():
        if len(answers) != expected:
            print(f"  Warning: {player} has {len(answers)} entries (expected {expected})")

    return all_data


def print_summary(data: dict):
    """Print a quick summary table of each player's season totals."""
    print(f"\n{'Player':<12} {'Correct':>7} {'Wrong':>7} {'Forfeit':>8} {'Total':>6}")
    print("-" * 44)
    for player, answers in sorted(data.items()):
        correct  = answers.count(1)
        wrong    = answers.count(0)
        forfeit  = answers.count("F")
        total    = len(answers)
        print(f"{player:<12} {correct:>7} {wrong:>7} {forfeit:>8} {total:>6}")


if __name__ == "__main__":
    ll = LearnedLeagueSession()
    ll.login(USERNAME, PASSWORD)

    print(f"\nScraping LL{SEASON} — {RUNDLE_NAME} ({NUM_DAYS} match days x {NUM_QUESTIONS} questions)\n")
    data = scrape_rundle(ll, season=SEASON, rundle_name=RUNDLE_NAME)

    print_summary(data)

    # data is your dict — use it however you like, e.g.:
    #   import json
    #   with open("rundle_data.json", "w") as f:
    #       json.dump(data, f, indent=2)

    import json
    with open("rundle_data.json", "w") as f:
        json.dump(data, f, indent=2)
