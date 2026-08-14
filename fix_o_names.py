"""
fix_o_names.py
---------------
Fix the "O" mislabeled-player bug in rundle_data_108.json / rundle_data_109.json.

Root cause (see debug_o_output.txt): parse_matchday() reads a player's
abbreviation from their standings row's <img alt="..."> attribute. For a
small number of players, an unrelated attribute on that same <img> (their
flag icon) comes back malformed from the site -- an embedded, unescaped
apostrophe corrupts the tag (e.g. a stray `sheat'=""` attribute) -- which
truncates `alt` down to just "O". The Q1-Q6 answer cells in that row are
unaffected, and the corruption is deterministic per player (same flag
image every time), so every affected rundle's "O" entry is one player's
complete, correct 150-answer array sitting under the wrong key.

The real abbreviation is the plain visible text in the name cell right
after the flag icon (e.g. "OShT"). This script fetches one match day per
affected rundle, reads that text, and renames the "O" key to it --
existing answer data is never refetched or overwritten for this, the
common, case. No other player in the rundle is touched.

Rare case: if a rundle has *two* different players who both hit this
corruption, their data already collided into a single "O" array during
the original scrape (a plain dict key collision) and one player's data
isn't recoverable by renaming -- for those rundles only, this script
re-scrapes all match days for just that rundle. Even then, the fixed
extractor only overrides rows whose alt is "O"; every other row uses the
exact same extraction (img alt) as the original scraper, so already-correct
labels in that rundle can't change.

Usage:
    python fix_o_names.py
"""

import getpass
import json
import os

from ll_session import LearnedLeagueSession
from ll_rundle_scraper import NUM_DAYS, NUM_QUESTIONS

DATA_FILE_TEMPLATE = os.path.join(os.path.dirname(__file__), "rundle_data_{season}.json")
SEASONS = [108, 109]
BASE_URL = "https://learnedleague.com"


def _corrected_name(name_cell) -> str:
    """The real player abbreviation from the visible text of a name cell."""
    return name_cell.get_text().replace("\xa0", "").strip()


def find_o_names(soup) -> list:
    """Real abbreviation (from visible text) for every row on this match
    day whose img alt extracted as "O"."""
    table = soup.find("table", class_="std")
    if table is None:
        return []
    found = []
    for row in table.find_all("tr"):
        name_cell = row.find("td", class_="std-midleft")
        if name_cell is None:
            continue
        img = name_cell.find("img")
        if img is None or img.get("alt") != "O":
            continue
        found.append(_corrected_name(name_cell))
    return found


def parse_matchday_fixed(soup) -> dict:
    """
    Same extraction as ll_rundle_scraper.parse_matchday, except a row whose
    img alt is "O" is relabeled using the visible cell text instead. Every
    other row is extracted identically to the original scraper -- this
    only touches the corrupted rows.
    """
    CLASS_MAP = {"c1": 1, "c0": 0, "cF": "F"}
    results = {}
    table = soup.find("table", class_="std")
    if table is None:
        return results

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
        player = img["alt"]
        if player == "O":
            player = _corrected_name(name_cell)

        if player:
            results[player] = answers

    return results


def scrape_rundle_fixed(ll, season, rundle_name, num_days=NUM_DAYS, num_questions=NUM_QUESTIONS):
    all_data = {}
    for day in range(1, num_days + 1):
        url = f"{BASE_URL}/match.php?{season}&{day}&{rundle_name}"
        print(f"    day {day:>2}/{num_days}")
        soup = ll.get_soup(url, validate=lambda s: s.find("table", class_="std") is not None)
        day_results = parse_matchday_fixed(soup)
        if not day_results:
            for player in all_data:
                all_data[player].extend([None] * num_questions)
        else:
            for player, answers in day_results.items():
                if player not in all_data:
                    all_data[player] = [None] * (day - 1) * num_questions
                all_data[player].extend(answers)
    return all_data


def main():
    username = os.environ.get("LL_USERNAME") or input("LL username: ").strip()
    password = os.environ.get("LL_PASSWORD") or getpass.getpass("LL password: ")

    ll = LearnedLeagueSession(headless=False)
    ll.login(username, password)

    renamed = []
    collisions = []
    unresolved = []

    try:
        for season in SEASONS:
            path = DATA_FILE_TEMPLATE.format(season=season)
            with open(path) as f:
                data = json.load(f)

            affected = sorted(r for r, players in data["rundles"].items() if "O" in players)
            print(f"\nLL{season}: {len(affected)} affected rundles")

            for i, rundle_name in enumerate(affected, 1):
                print(f"  [{i}/{len(affected)}] {rundle_name} ...", end=" ")

                o_names = []
                for day in range(1, NUM_DAYS + 1):
                    url = f"{BASE_URL}/match.php?{season}&{day}&{rundle_name}"
                    soup = ll.get_soup(url, validate=lambda s: s.find("table", class_="std") is not None)
                    o_names = find_o_names(soup)
                    if o_names:
                        break

                if not o_names:
                    print("no alt='O' row found on any day -- skipping (needs manual check)")
                    unresolved.append((season, rundle_name))
                    continue

                distinct = sorted(set(o_names))
                if len(distinct) == 1:
                    corrected = distinct[0]
                    if corrected in data["rundles"][rundle_name]:
                        print(f"corrected name {corrected!r} already exists in this rundle -- skipping (needs manual check)")
                        unresolved.append((season, rundle_name))
                        continue
                    data["rundles"][rundle_name][corrected] = data["rundles"][rundle_name].pop("O")
                    print(f"renamed 'O' -> {corrected!r}")
                    renamed.append((season, rundle_name, corrected))
                    with open(path, "w") as f:
                        json.dump(data, f, indent=2)
                else:
                    print(f"{len(distinct)} distinct players collided ({distinct}) -- re-scraping this rundle")
                    collisions.append((season, rundle_name))

            # Resolve any genuine collisions for this season with a full
            # (fixed-extractor) re-scrape of just those rundles.
            for s, rundle_name in [c for c in collisions if c[0] == season]:
                print(f"\n  Re-scraping {rundle_name} (LL{s}) to resolve a name collision ...")
                fresh = scrape_rundle_fixed(ll, season=s, rundle_name=rundle_name)
                data["rundles"][rundle_name] = fresh
                with open(path, "w") as f:
                    json.dump(data, f, indent=2)
                print(f"  Replaced {rundle_name} with {len(fresh)} players from fresh scrape.")
    finally:
        ll.close()

    print(f"\nDone. {len(renamed)} renamed, {len(collisions)} re-scraped, {len(unresolved)} unresolved.")
    if unresolved:
        print("Unresolved (needs manual look):", unresolved)


if __name__ == "__main__":
    main()
