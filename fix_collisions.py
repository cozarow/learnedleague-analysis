"""
fix_collisions.py
-------------------
For rundles where guess_o_names.py found more than one player corrupted
to the same truncated letter (a "COLLISION" note in the worklist), a
straight rename isn't enough -- during the original scrape both players'
rows landed on the same dict key, so one of them was silently
overwritten. This re-scrapes just those rundles, distinguishing the
colliding players by profile id (from the flag link's href, e.g.
/profiles.php?90028) instead of by name, reconstructs each one's real
name the same way guess_o_names.py does, and replaces the whole rundle
entry with the corrected data.

Every other (non-corrupted) player in these rundles is extracted exactly
as the original scraper would (same alt-based lookup), so re-scraping
only changes the previously-collided entries.

Usage:
    python fix_collisions.py
"""

import getpass
import json
import os
import re
import time

from ll_session import LearnedLeagueSession
from ll_rundle_scraper import NUM_DAYS, NUM_QUESTIONS
from guess_o_names import reconstruct, KNOWN_IMG_ATTRS

DATA_FILE_TEMPLATE = os.path.join(os.path.dirname(__file__), "rundle_data_{season}.json")
WORKLIST_TEMPLATE = os.path.join(os.path.dirname(__file__), "o_fix_worklist_{season}.txt")
SEASONS = [108, 109]
BASE_URL = "https://learnedleague.com"
DELAY = 0.4

PROFILE_ID_RE = re.compile(r"profiles\.php\?(\d+)")


def find_collision_rundles(season):
    path = WORKLIST_TEMPLATE.format(season=season)
    rundles = []
    with open(path) as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) > 3 and parts[3].startswith("COLLISION"):
                rundles.append(parts[0])
    return rundles


def parse_matchday_by_profile(soup):
    """
    Like ll_rundle_scraper.parse_matchday, but rows whose alt is a single
    truncated letter are keyed by profile id instead of name, with their
    (alt, leftover attribute) captured for later reconstruction. Every
    other row is keyed by alt exactly as the original scraper does.
    """
    CLASS_MAP = {"c1": 1, "c0": 0, "cF": "F"}
    results = {}
    corrupted_info = {}

    table = soup.find("table", class_="std")
    if table is None:
        return results, corrupted_info

    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 8:
            continue

        q_cells = cells[:6]
        answers = []
        is_player_row = True
        for td in q_cells:
            cls = td.get("class", [])
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

        name_cell = row.find("td", class_="std-midleft")
        if name_cell is None:
            continue

        img = name_cell.find("img")
        alt = img["alt"]

        if len(alt) == 1 and alt.isalpha():
            a = name_cell.find("a")
            href = a["href"] if a else ""
            m = PROFILE_ID_RE.search(href)
            if m is None:
                continue  # can't identify -- skip rather than mix into another player's data
            profile_id = int(m.group(1))
            leftover_keys = [k for k in img.attrs if k not in KNOWN_IMG_ATTRS]
            leftover = leftover_keys[0] if leftover_keys else None
            corrupted_info.setdefault(profile_id, (alt, leftover))
            results[profile_id] = answers
        else:
            results[alt] = answers

    return results, corrupted_info


def scrape_rundle_by_profile(ll, season, rundle_name):
    all_data = {}
    corrupted_info = {}

    for day in range(1, NUM_DAYS + 1):
        url = f"{BASE_URL}/match.php?{season}&{day}&{rundle_name}"
        print(f"    day {day:>2}/{NUM_DAYS}")
        soup = ll.get_soup(url, validate=lambda s: s.find("table", class_="std") is not None)
        time.sleep(DELAY)

        day_results, day_corrupted = parse_matchday_by_profile(soup)
        corrupted_info.update(day_corrupted)

        if not day_results:
            for key in all_data:
                all_data[key].extend([None] * NUM_QUESTIONS)
        else:
            for key, answers in day_results.items():
                if key not in all_data:
                    all_data[key] = [None] * (day - 1) * NUM_QUESTIONS
                all_data[key].extend(answers)

    final = {}
    for key, answers in all_data.items():
        if isinstance(key, int):
            alt, leftover = corrupted_info.get(key, (None, None))
            name = reconstruct(alt, leftover) if (alt and leftover) else None
            if name is None:
                name = f"UNRESOLVED_{key}"
                print(f"    ! could not reconstruct a name for profile {key} -- placeholder {name!r}, needs manual fix")
            final[name] = answers
        else:
            final[key] = answers
    return final


def main():
    username = os.environ.get("LL_USERNAME") or input("LL username: ").strip()
    password = os.environ.get("LL_PASSWORD") or getpass.getpass("LL password: ")

    ll = LearnedLeagueSession(headless=False)
    ll.login(username, password)

    try:
        for season in SEASONS:
            rundles = find_collision_rundles(season)
            if not rundles:
                print(f"LL{season}: no collision rundles to fix.")
                continue
            print(f"\nLL{season}: {len(rundles)} collision rundles to re-scrape")

            data_path = DATA_FILE_TEMPLATE.format(season=season)
            with open(data_path) as f:
                data = json.load(f)

            for i, rundle_name in enumerate(rundles, 1):
                print(f"  [{i}/{len(rundles)}] {rundle_name}")
                fresh = scrape_rundle_by_profile(ll, season, rundle_name)
                data["rundles"][rundle_name] = fresh
                with open(data_path, "w") as f:
                    json.dump(data, f, indent=2)
                print(f"    replaced with {len(fresh)} players: {sorted(fresh.keys())}")
    finally:
        ll.close()

    print("\nDone.")


if __name__ == "__main__":
    main()
