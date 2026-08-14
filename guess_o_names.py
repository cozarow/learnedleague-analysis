"""
guess_o_names.py
-------------------
Auto-fills the worklist's corrected_name column with a best-effort
reconstruction of each affected player's real abbreviated code, so you
only need to skim/verify rather than look each one up by hand.

How the reconstruction works
-----------------------------
The corruption comes from the site's HTML containing something like
alt='O'SheaT' -- a single-quoted attribute whose value itself has an
embedded apostrophe. Per the HTML5 tokenizer spec, that embedded quote
ends the attribute early (alt="O"), and whatever came after it gets
re-tokenized as a bogus, lowercased, empty-valued attribute name on the
same tag (e.g. sheat'="") -- that's the stray attribute visible if you
inspect one of these rows.

That stray attribute name is a character-complete (if lowercased) copy of
everything that followed the embedded apostrophe. Combined with the very
consistent naming convention used throughout this dataset (capitalized
first letter of the abbreviated last name, capitalized first initial,
optional trailing digits -- e.g. "SchE", "KeJ4", "MaD"), the leftover
text can be split back into [last-name remainder][first initial][digits]
and re-capitalized with high confidence.

This is still a heuristic, not a certainty -- skim the corrected_name
column before running apply_o_fixes.py. Rows this script can't
confidently reconstruct, or where two different players collided into
the same "O" entry, are left blank with a note in a 4th column.

Usage:
    python guess_o_names.py
"""

import getpass
import os
import re
import time

from ll_session import LearnedLeagueSession
from ll_rundle_scraper import NUM_DAYS

WORKLIST_TEMPLATE = os.path.join(os.path.dirname(__file__), "o_fix_worklist_{season}.txt")
SEASONS = [108, 109]
BASE_URL = "https://learnedleague.com"
DELAY = 0.4

# Attributes normally present on a flagimg tag (from an unaffected row) --
# anything else on the tag is the leftover corruption artifact.
KNOWN_IMG_ATTRS = {"align", "alt", "class", "height", "src", "title", "width"}


def reconstruct(alt_value: str, leftover_key: str):
    tail = leftover_key.rstrip("'")
    m = re.match(r"^([a-z]+)([a-z])(\d*)$", tail)
    if not m:
        return None
    last_part, initial, digits = m.groups()
    return f"{alt_value}'{last_part.capitalize()}{initial.upper()}{digits}"


def find_corrupted_rows(soup):
    """[(alt_value, leftover_key_or_None), ...] for every row on this page
    whose img alt looks like a single truncated letter."""
    table = soup.find("table", class_="std")
    if table is None:
        return []
    found = []
    for row in table.find_all("tr"):
        name_cell = row.find("td", class_="std-midleft")
        if name_cell is None:
            continue
        img = name_cell.find("img")
        if img is None:
            continue
        alt = img.get("alt", "")
        if len(alt) != 1 or not alt.isalpha():
            continue
        leftover_keys = [k for k in img.attrs if k not in KNOWN_IMG_ATTRS]
        leftover = leftover_keys[0] if leftover_keys else None
        found.append((alt, leftover))
    return found


def process_worklist(ll, season):
    path = WORKLIST_TEMPLATE.format(season=season)
    with open(path) as f:
        lines = f.readlines()

    out_lines = []
    for line in lines:
        stripped = line.rstrip("\n")
        if not stripped or stripped.startswith("#"):
            out_lines.append(line if line.endswith("\n") else line + "\n")
            continue

        parts = stripped.split("\t")
        rundle_name, url = parts[0], parts[1]
        existing_correction = parts[2] if len(parts) > 2 else ""

        if existing_correction.strip():
            out_lines.append(line if line.endswith("\n") else line + "\n")
            continue

        print(f"  LL{season} {rundle_name} ...", end=" ")
        found = []
        for day in range(1, NUM_DAYS + 1):
            day_url = f"{BASE_URL}/match.php?{season}&{day}&{rundle_name}"
            soup = ll.get_soup(day_url, validate=lambda s: s.find("table", class_="std") is not None)
            time.sleep(DELAY)
            found = find_corrupted_rows(soup)
            if found:
                break

        if not found:
            print("no corrupted row found -- leaving blank")
            out_lines.append(f"{rundle_name}\t{url}\t\tNOT FOUND on any day\n")
            continue

        distinct = sorted(set(found))
        if len(distinct) > 1:
            print(f"{len(distinct)} distinct corrupted players -- collision, leaving blank")
            out_lines.append(f"{rundle_name}\t{url}\t\tCOLLISION: {distinct}\n")
            continue

        alt_value, leftover = distinct[0]
        if leftover is None:
            print("no leftover attribute found -- leaving blank")
            out_lines.append(f"{rundle_name}\t{url}\t\tno leftover attribute found\n")
            continue

        guess = reconstruct(alt_value, leftover)
        if guess is None:
            print(f"couldn't parse leftover {leftover!r} -- leaving blank")
            out_lines.append(f"{rundle_name}\t{url}\t\tcouldn't parse leftover {leftover!r}\n")
            continue

        print(f"guess: {guess!r}")
        out_lines.append(f"{rundle_name}\t{url}\t{guess}\n")

    with open(path, "w") as f:
        f.writelines(out_lines)


def main():
    username = os.environ.get("LL_USERNAME") or input("LL username: ").strip()
    password = os.environ.get("LL_PASSWORD") or getpass.getpass("LL password: ")

    ll = LearnedLeagueSession(headless=False)
    ll.login(username, password)
    try:
        for season in SEASONS:
            print(f"\nLL{season}:")
            process_worklist(ll, season)
    finally:
        ll.close()

    print("\nDone -- corrected_name columns filled in with best-effort guesses.")
    print("Skim o_fix_worklist_108.txt / o_fix_worklist_109.txt before running apply_o_fixes.py.")


if __name__ == "__main__":
    main()
