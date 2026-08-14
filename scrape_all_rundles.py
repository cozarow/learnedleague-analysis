"""
scrape_all_rundles.py
----------------------
Scrape every rundle in a LearnedLeague season and save all players' answer
data, nested by rundle, to rundle_data.json.

A full season has 1000+ rundles, so this can take several hours. Progress
is saved after every single rundle finishes, so it's safe to interrupt
(Ctrl+C) and rerun: already-scraped rundles are skipped automatically.

If a page request looks like it's being rate-limited or blocked (including
a "soft" block: a normal-looking 200 OK page that isn't actually the real
standings page), the underlying session (see ll_session.py) already waits
~5 minutes and retries a few times on its own. If a rundle still can't
get through after that, this script logs it, skips that rundle entirely
(so a partially-blocked fetch never gets saved as if it were complete),
and moves on to the rest. Skipped rundles are listed at the end and will
be retried automatically the next time you run this script.

Each season is saved to its own file, rundle_data_<season>.json:
    {
        "season": 109,
        "num_days": 25,
        "num_questions": 6,
        "rundles": {
            "A_Aloha":   {"SchE": [1, 1, 0, ..., "F"], "KeJ4": [...], ...},
            "B_Cypress": {...},
            ...
        }
    }

Each rundle's sub-dict is a { player_abbrev: [150 elements of 1/0/"F"] }
mapping in the same shape ll_analysis.py already expects, so within-rundle
comparisons (e.g. one_pairwise_agreement) just need data["rundles"][name]
handed straight to the existing functions. For comparisons across the
whole season, merge the per-rundle dicts together (player abbreviations
are unique per player, so this is safe). Seasons are kept in separate
files rather than merged together -- see ll_all_rundle_analysis.get_tables,
which takes a `season` keyword to pick which file/tables to work with.

Usage:
    python scrape_all_rundles.py
    SEASON=109 python scrape_all_rundles.py   # override the season below

Credentials are normally entered interactively, but can also be supplied
via the LL_USERNAME / LL_PASSWORD environment variables (e.g. to run this
unattended/in the background without typing a password into a terminal
someone else can see).
"""

import getpass
import json
import os

from ll_session import LearnedLeagueSession, RateLimitedError
from ll_rundle_scraper import discover_rundles, scrape_rundle, NUM_DAYS, NUM_QUESTIONS

# ── Config ────────────────────────────────────────────────────────────
SEASON = int(os.environ.get("LL_SEASON", 109))
DELAY  = 0.4  # seconds between match-day requests within a rundle
# ──────────────────────────────────────────────────────────────────────

DATA_FILE = os.path.join(os.path.dirname(__file__), f"rundle_data_{SEASON}.json")


def load_progress() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE) as f:
            state = json.load(f)

        if isinstance(state, dict) and "rundles" in state:
            print(f"Resuming from {DATA_FILE}: {len(state['rundles'])} rundles already done.")
            return state

        # Existing file is in some other (e.g. old single-rundle) format --
        # don't touch it, don't try to resume from it. Move it aside so we
        # start clean without losing whatever was there.
        backup = DATA_FILE + ".bak"
        print(f"{DATA_FILE} is in an older/different format -- moving it to {backup} and starting fresh.")
        os.replace(DATA_FILE, backup)

    return {
        "season": SEASON,
        "num_days": NUM_DAYS,
        "num_questions": NUM_QUESTIONS,
        "rundles": {},
    }


def save_progress(state: dict):
    # Write to a temp file and rename so a crash mid-write can't corrupt
    # the existing (still-good) rundle_data.json.
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, DATA_FILE)


def main():
    username = os.environ.get("LL_USERNAME") or input("LL username: ").strip()
    password = os.environ.get("LL_PASSWORD") or getpass.getpass("LL password: ")

    state = load_progress()

    ll = LearnedLeagueSession()
    ll.login(username, password)

    print(f"\nDiscovering rundles for season {SEASON} ...")
    rundles = discover_rundles(ll, SEASON)
    remaining = [r for r in rundles if r not in state["rundles"]]
    print(f"Found {len(rundles)} rundles total. "
          f"{len(rundles) - len(remaining)} already done, {len(remaining)} to go.\n")

    skipped = []
    try:
        for i, rundle_name in enumerate(remaining, 1):
            print(f"[{i}/{len(remaining)}] Scraping rundle {rundle_name} ...")

            try:
                rundle_data = scrape_rundle(
                    ll,
                    season=SEASON,
                    rundle_name=rundle_name,
                    num_days=NUM_DAYS,
                    num_questions=NUM_QUESTIONS,
                    delay=DELAY,
                )
            except RateLimitedError as e:
                print(f"  Giving up on {rundle_name} for now: {e}")
                print("  Skipping it -- it will be retried the next time this script runs.")
                skipped.append(rundle_name)
                continue

            state["rundles"][rundle_name] = rundle_data
            save_progress(state)
    finally:
        ll.close()

    total_players = sum(len(players) for players in state["rundles"].values())
    print(f"\nDone. {total_players} players across {len(state['rundles'])} rundles saved to {DATA_FILE}")
    if skipped:
        print(f"{len(skipped)} rundle(s) were skipped due to persistent errors: {', '.join(skipped)}")
        print("Rerun this script to retry them.")


if __name__ == "__main__":
    main()
