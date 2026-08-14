"""
apply_o_fixes.py
------------------
Applies manually-looked-up corrected names to rundle_data_108.json /
rundle_data_109.json, reading from o_fix_worklist_108.txt /
o_fix_worklist_109.txt.

Each worklist file has one tab-separated line per rundle currently
containing an "O" entry:
    rundle_name<TAB>url<TAB>corrected_name

Fill in the third column (corrected_name) for whichever rows you've
looked up -- rows left blank are skipped, so it's fine to run this
multiple times as you fill in more of the list incrementally.

For each filled-in row, this renames the "O" key to the corrected name in
that rundle's dict -- the existing 150-answer array is preserved exactly
as-is, and no other player in the rundle is touched.

Usage:
    python apply_o_fixes.py
"""

import json
import os

DATA_FILE_TEMPLATE = os.path.join(os.path.dirname(__file__), "rundle_data_{season}.json")
WORKLIST_TEMPLATE = os.path.join(os.path.dirname(__file__), "o_fix_worklist_{season}.txt")
SEASONS = [108, 109]


def load_worklist(season: int) -> list:
    path = WORKLIST_TEMPLATE.format(season=season)
    rows = []
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            rundle_name, url, corrected_name = parts[0], parts[1], parts[2].strip()
            if corrected_name:
                rows.append((rundle_name, corrected_name))
    return rows


def main():
    for season in SEASONS:
        rows = load_worklist(season)
        if not rows:
            print(f"LL{season}: no filled-in corrections yet, skipping.")
            continue

        data_path = DATA_FILE_TEMPLATE.format(season=season)
        with open(data_path) as f:
            data = json.load(f)

        applied = 0
        for rundle_name, corrected_name in rows:
            rundle = data["rundles"].get(rundle_name)
            if rundle is None:
                print(f"  LL{season} {rundle_name}: rundle not found -- skipping")
                continue
            if "O" not in rundle:
                print(f"  LL{season} {rundle_name}: no 'O' entry (already fixed?) -- skipping")
                continue
            if corrected_name in rundle:
                print(f"  LL{season} {rundle_name}: {corrected_name!r} already exists in this rundle -- skipping, needs manual look")
                continue
            rundle[corrected_name] = rundle.pop("O")
            applied += 1
            print(f"  LL{season} {rundle_name}: 'O' -> {corrected_name!r}")

        if applied:
            with open(data_path, "w") as f:
                json.dump(data, f, indent=2)
        print(f"LL{season}: applied {applied}/{len(rows)} corrections.\n")


if __name__ == "__main__":
    main()
