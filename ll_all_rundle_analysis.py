"""
ll_all_rundle_analysis.py
--------------------------
Pairwise-agreement analysis across *every* player in every rundle of a
season, using rundle_data.json (produced by scrape_all_rundles.py).

rundle_data.json shape:
    {
      "season": int,
      "num_days": int,
      "num_questions": int,           # per day
      "rundles": {
          "<rundle_name>": {
              "<player_name>": [1, 0, "F", ...],   # one entry per question
              ...
          },
          ...
      }
    }

Every player in a season answers the same sequence of questions (num_days *
num_questions of them, e.g. 150), regardless of rundle -- so pairwise
agreement is meaningful both within a rundle and across the whole season.

Two player rows can legitimately share the same display name across
different rundles (this data also contains a couple of scraper artifacts --
e.g. a name "O" recurring across ~140 unrelated rundles with different
answers each time). To stay correct, every row is keyed by
(rundle, player_name), not by name alone. Empty rundle entries (scraper
artifacts with zero players) are skipped when the tables are built.

Design
------
A literal 3D tensor of shape (N, N, Q) -- one boolean/int layer per
question -- would take ~N^2*Q bytes, which for N ~ 35,000 and Q = 150 is on
the order of 150+ GB even at 1 byte/cell. That's not viable.

Instead we get the same pairwise-agreement table via two matrix
multiplications instead of looping over Q layers by hand:

  - Encode each player's Q answers as a vector in {-1, 0, +1}:
        S[i, q] =  0 if forfeited
                  +1 if correct
                  -1 if incorrect
    and a played-mask P[i, q] in {0, 1} (1 unless forfeited).

  - For a pair (i, j) and question q: S[i,q]*S[j,q] is +1 if both played and
    agreed, -1 if both played and disagreed, 0 if either forfeited. Summed
    over q, dotS[i,j] = agree_count - disagree_count.
  - P[i,q]*P[j,q] is 1 iff both played question q. Summed over q,
    dotP[i,j] = agree_count + disagree_count = "valid" comparison count.
  - So agree_count = (dotS + dotP) / 2, and dotS = S @ S.T, dotP = P @ P.T
    are each a single (N, Q) @ (Q, N) matrix multiply -- BLAS-parallelized
    across all CPU cores, computed once for the whole dataset.

The resulting (N, N) agree/valid count tables are stored as int16 (counts
never exceed Q <= a few hundred), ~2.5 GB each for N ~ 36,000 -- easily
resident in memory. one_pairwise_agreement, average_agreement,
most_similar_pairs, etc. then just read out of these precomputed tables.

Seasons are kept in separate files, rundle_data_<season>.json (see
scrape_all_rundles.py), and analyzed independently -- there's no
cross-season comparison. The `season` keyword picks which one to work
with:

    from ll_all_rundle_analysis import get_tables, most_similar_pairs
    tables108 = get_tables(season=108)
    tables109 = get_tables(season=109)
    most_similar_pairs(tables109, n=10)

`get_tables` caches the built tables per season in-process, since building
them is the expensive step (~seconds, multi-GB temp arrays); everything
else here is a cheap lookup against the tables you pass in.
"""

import json
import os
from dataclasses import dataclass

import numpy as np

DATA_FILE_TEMPLATE = os.path.join(os.path.dirname(__file__), "rundle_data_{season}.json")


# ---------------------------------------------------------------------------
# Table construction
# ---------------------------------------------------------------------------

_S_MAP = {0: -1, 1: 1, "F": 0}
_P_MAP = {0: 1, 1: 1, "F": 0}


@dataclass
class AgreementTables:
    """
    Precomputed pairwise-agreement tables for every (rundle, player) row in
    a season.

    labels[i]        -> (rundle_name, player_name) for row i
    tiers[i]          -> the rundle's tier/branch prefix (e.g. "A", "R") for row i
    index_by_name      -> player_name -> list of row indices (usually length 1)
    index_by_rundle     -> rundle_name -> list of row indices
    index_by_tier    -> tier -> list of row indices
    agree[i, j]       -> count of questions where i and j both played and agreed
    valid[i, j]       -> count of questions where i and j both played (denominator)
    num_questions     -> total questions in the season (Q); valid[i, i] is
                         player i's own non-forfeit count, since a player
                         trivially "agrees with themselves" on every
                         question they played.
    correct_counts[i] -> count of questions player i answered correctly
                         (catches rows that are all-0 -- e.g. a scraper
                         artifact rather than a real forfeit -- which would
                         otherwise look like a legitimately "similar"
                         player to everyone else who also missed a lot).
    """

    labels: list
    tiers: list
    index_by_name: dict
    index_by_rundle: dict
    index_by_tier: dict
    agree: np.ndarray
    valid: np.ndarray
    num_questions: int
    correct_counts: np.ndarray

    def __len__(self):
        return len(self.labels)

    def non_forfeit_counts(self) -> np.ndarray:
        """Per-row count of non-forfeited questions (diagonal of `valid`)."""
        return np.diagonal(self.valid)


def _tier_of(rundle_name: str) -> str:
    return rundle_name.split("_", 1)[0] if "_" in rundle_name else rundle_name


def build_agreement_tables(data: dict) -> AgreementTables:
    """
    Build the full pairwise-agreement tables for every player in every
    (non-empty) rundle in `data`.
    """
    rundles = data["rundles"]

    labels = []
    tiers = []
    rows = []
    for rundle_name in sorted(rundles):
        players = rundles[rundle_name]
        if not players:
            continue  # skip empty scraper-artifact rundle entries
        tier = _tier_of(rundle_name)
        for player_name in sorted(players):
            labels.append((rundle_name, player_name))
            tiers.append(tier)
            rows.append(players[player_name])

    n = len(rows)
    q = len(rows[0])

    S = np.empty((n, q), dtype=np.int8)
    P = np.empty((n, q), dtype=np.int8)
    for i, seq in enumerate(rows):
        S[i] = [_S_MAP[v] for v in seq]
        P[i] = [_P_MAP[v] for v in seq]

    # Use float32 (not integer dtypes) so the matmul goes through BLAS and
    # is multi-threaded; Q <= a few hundred so sums stay exactly
    # representable in float32.
    Sf = S.astype(np.float32)
    Pf = P.astype(np.float32)
    # macOS Accelerate's SGEMM trips benign divide-by-zero/overflow FP flags on
    # matmuls this large; verified bit-exact against an int64 reference, so
    # these warnings are cosmetic and safe to suppress.
    with np.errstate(all="ignore"):
        dot_s = Sf @ Sf.T
        dot_p = Pf @ Pf.T
    del Sf, Pf

    agree = np.rint((dot_s + dot_p) / 2).astype(np.int16)
    valid = np.rint(dot_p).astype(np.int16)
    del dot_s, dot_p

    # Same "sum instead of dot-product" trick as agree/valid, applied to a
    # single row: (row_sum(S) + row_sum(P)) / 2 counts the +1 (correct)
    # entries, since correct contributes (1+1)/2=1, incorrect (-1+1)/2=0,
    # forfeit (0+0)/2=0.
    correct_counts = ((S.astype(np.int32).sum(axis=1) + P.astype(np.int32).sum(axis=1)) // 2).astype(np.int16)

    index_by_name = {}
    index_by_rundle = {}
    index_by_tier = {}
    for i, ((rundle_name, player_name), tier) in enumerate(zip(labels, tiers)):
        index_by_name.setdefault(player_name, []).append(i)
        index_by_rundle.setdefault(rundle_name, []).append(i)
        index_by_tier.setdefault(tier, []).append(i)

    return AgreementTables(
        labels=labels,
        tiers=tiers,
        index_by_name=index_by_name,
        index_by_rundle=index_by_rundle,
        index_by_tier=index_by_tier,
        agree=agree,
        valid=valid,
        num_questions=q,
        correct_counts=correct_counts,
    )


_tables_cache: dict = {}


def load_season_data(season: int) -> dict:
    """Load the raw rundle_data_<season>.json dict for a season."""
    path = DATA_FILE_TEMPLATE.format(season=season)
    with open(path) as f:
        return json.load(f)


def get_tables(season: int, use_cache: bool = True) -> AgreementTables:
    """
    Load rundle_data_<season>.json and build its AgreementTables (or return
    the already-built tables from an in-process cache). This is the main
    entry point for interactive use -- pass season=108 or season=109 to
    pick which season all downstream analysis functions (pairwise_agreement,
    most_similar_pairs, etc.) operate on.
    """
    if use_cache and season in _tables_cache:
        return _tables_cache[season]
    tables = build_agreement_tables(load_season_data(season))
    if use_cache:
        _tables_cache[season] = tables
    return tables


def save_agreement_tables(tables: AgreementTables, path: str) -> None:
    """
    Persist tables to disk (as an .npz, uncompressed) so they don't need to
    be rebuilt every run. Note the count tables alone are ~2.5 GB each for
    a full season (~5 GB total on disk) -- only call this if you have the
    disk space and want to skip the ~seconds-long rebuild.
    """
    np.savez(
        path,
        agree=tables.agree,
        valid=tables.valid,
        labels=np.array(tables.labels, dtype=object),
        tiers=np.array(tables.tiers, dtype=object),
        num_questions=np.array(tables.num_questions),
        correct_counts=tables.correct_counts,
    )


def load_agreement_tables(path: str) -> AgreementTables:
    with np.load(path, allow_pickle=True) as f:
        labels = [tuple(x) for x in f["labels"]]
        tiers = list(f["tiers"])
        agree = f["agree"]
        valid = f["valid"]
        num_questions = int(f["num_questions"])
        correct_counts = f["correct_counts"]

    index_by_name = {}
    index_by_rundle = {}
    index_by_tier = {}
    for i, ((rundle_name, player_name), tier) in enumerate(zip(labels, tiers)):
        index_by_name.setdefault(player_name, []).append(i)
        index_by_rundle.setdefault(rundle_name, []).append(i)
        index_by_tier.setdefault(tier, []).append(i)

    return AgreementTables(
        labels=labels,
        tiers=tiers,
        index_by_name=index_by_name,
        index_by_rundle=index_by_rundle,
        index_by_tier=index_by_tier,
        agree=agree,
        valid=valid,
        num_questions=num_questions,
        correct_counts=correct_counts,
    )


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

def _resolve_index(tables: AgreementTables, player: str, rundle: str = None) -> int:
    idxs = tables.index_by_name.get(player)
    if not idxs:
        raise KeyError(f"Unknown player: {player!r}")
    if rundle is not None:
        idxs = [i for i in idxs if tables.labels[i][0] == rundle]
        if not idxs:
            raise KeyError(f"Player {player!r} not found in rundle {rundle!r}")
    if len(idxs) > 1:
        candidates = sorted(tables.labels[i][0] for i in idxs)
        raise ValueError(
            f"Player name {player!r} is ambiguous across rundles {candidates}; "
            f"pass rundle=... to disambiguate"
        )
    return idxs[0]


def _resolve_scope(tables: AgreementTables, rundle: str = None, tier: str = None) -> np.ndarray:
    if rundle is not None and tier is not None:
        raise ValueError("pass only one of rundle= or tier=")
    if rundle is not None:
        idxs = tables.index_by_rundle.get(rundle)
        if not idxs:
            raise KeyError(f"Unknown rundle: {rundle!r}")
        return np.array(idxs)
    if tier is not None:
        idxs = tables.index_by_tier.get(tier)
        if not idxs:
            raise KeyError(f"Unknown tier: {tier!r}")
        return np.array(idxs)
    return np.arange(len(tables.labels))


def _filter_players(tables: AgreementTables, idxs: np.ndarray,
                     min_non_forfeit: int, min_correct: int) -> np.ndarray:
    """
    Restrict `idxs` to rows that both:
      - have a non-forfeit count >= min_non_forfeit (forfeits are recorded
        in whole days, so thresholds in practice land on multiples of the
        per-day question count), and
      - have a correct-answer count >= min_correct. This weeds out rows
        that are all-0 rather than genuinely forfeited -- e.g. a scraper
        artifact -- which would otherwise look spuriously "similar" to
        other low scorers.
    """
    played = tables.non_forfeit_counts()[idxs]
    correct = tables.correct_counts[idxs]
    return idxs[(played >= min_non_forfeit) & (correct >= min_correct)]


# ---------------------------------------------------------------------------
# Analysis functions (mirror ll_analysis.py's API, scoped over the full
# season and/or filterable by rundle/tier)
# ---------------------------------------------------------------------------

def pairwise_agreement(tables: AgreementTables, p1: str, p2: str,
                        rundle1: str = None, rundle2: str = None):
    """
    O(1) lookup of the precomputed agreement fraction between two players.
    Returns None if they never both answered the same question.
    """
    i = _resolve_index(tables, p1, rundle1)
    j = _resolve_index(tables, p2, rundle2)
    v = int(tables.valid[i, j])
    if v == 0:
        return None
    return int(tables.agree[i, j]) / v


def one_pairwise_agreement(tables: AgreementTables, player: str, rundle: str = None,
                            scope_rundle: str = None, scope_tier: str = None,
                            min_non_forfeit: int = 150, min_correct: int = 15) -> list:
    """
    Agreement between `player` and every other player in scope (default:
    the whole season), sorted descending. Pure table lookup -- no
    recomputation.

    min_non_forfeit: only include other players who themselves have at
    least this many non-forfeit answers (default: tables.num_questions,
    i.e. no forfeits at all). Since forfeits are recorded in whole days,
    this threshold in practice only matters in multiples of the
    per-day question count.
    min_correct: only include other players who themselves answered at
    least this many questions correctly (default 15) -- filters out rows
    that are all-0 rather than genuinely forfeited.

    Returns a list of ((rundle, player_name), fraction).
    """
    i = _resolve_index(tables, player, rundle)
    scope = _resolve_scope(tables, scope_rundle, scope_tier)
    scope = _filter_players(tables, scope, min_non_forfeit, min_correct)

    valid_row = tables.valid[i, scope].astype(np.float64)
    agree_row = tables.agree[i, scope].astype(np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        frac = np.where(valid_row > 0, agree_row / valid_row, np.nan)

    order = np.argsort(-np.where(np.isnan(frac), -np.inf, frac))
    result = []
    for k in order:
        j = scope[k]
        if j == i or np.isnan(frac[k]):
            continue
        result.append((tables.labels[j], float(frac[k])))
    return result


def average_agreement(tables: AgreementTables, rundle: str = None, tier: str = None,
                       min_non_forfeit: int = 150, min_correct: int = 15) -> list:
    """
    For each qualifying player in scope, the average of their pairwise
    agreement fraction with every other qualifying player in scope. Returns
    a list of ((rundle, player_name), avg_or_None) sorted descending (None
    last).

    min_non_forfeit: only include players who themselves have at least
    this many non-forfeit answers (default: tables.num_questions, i.e. no
    forfeits at all).
    min_correct: only include players who themselves answered at least
    this many questions correctly (default 15) -- filters out rows that
    are all-0 rather than genuinely forfeited, which would otherwise drag
    down (or spuriously inflate) everyone else's average too.
    """
    idxs = _resolve_scope(tables, rundle, tier)
    idxs = _filter_players(tables, idxs, min_non_forfeit, min_correct)
    agree = tables.agree[np.ix_(idxs, idxs)].astype(np.float32)
    valid = tables.valid[np.ix_(idxs, idxs)].astype(np.float32)

    frac = np.full(agree.shape, np.nan, dtype=np.float32)
    np.divide(agree, valid, out=frac, where=valid > 0)
    np.fill_diagonal(frac, np.nan)

    with np.errstate(invalid="ignore"):
        row_avg = np.nanmean(frac, axis=1)

    result = [
        (tables.labels[idxs[k]], None if np.isnan(row_avg[k]) else float(row_avg[k]))
        for k in range(len(idxs))
    ]
    result.sort(key=lambda x: (x[1] is None, -(x[1] if x[1] is not None else 0.0)))
    return result


def top_agreement(tables: AgreementTables, rundle: str = None, tier: str = None,
                   min_non_forfeit: int = 150, min_correct: int = 15) -> list:
    """
    For each qualifying player in scope, their highest pairwise agreement
    fraction with any other qualifying player in scope (i.e. the fraction
    from that player's most_similar_player). Returns a list of
    ((rundle, player_name), max_or_None) sorted descending (None last).

    To get the average/minimum top agreement across scope:
        vals = [v for _, v in top_agreement(tables) if v is not None]
        avg_top = sum(vals) / len(vals)
        min_top = min(vals)   # equivalently vals[-1], since sorted descending

    min_non_forfeit: only include players who themselves have at least
    this many non-forfeit answers (default: tables.num_questions, i.e. no
    forfeits at all).
    min_correct: only include players who themselves answered at least
    this many questions correctly (default 15) -- filters out rows that
    are all-0 rather than genuinely forfeited, which would otherwise look
    spuriously "similar" to other low scorers.
    """
    idxs = _resolve_scope(tables, rundle, tier)
    idxs = _filter_players(tables, idxs, min_non_forfeit, min_correct)
    agree = tables.agree[np.ix_(idxs, idxs)].astype(np.float32)
    valid = tables.valid[np.ix_(idxs, idxs)].astype(np.float32)

    frac = np.full(agree.shape, np.nan, dtype=np.float32)
    np.divide(agree, valid, out=frac, where=valid > 0)
    np.fill_diagonal(frac, np.nan)

    with np.errstate(invalid="ignore"):
        row_max = np.nanmax(frac, axis=1)

    result = [
        (tables.labels[idxs[k]], None if np.isnan(row_max[k]) else float(row_max[k]))
        for k in range(len(idxs))
    ]
    result.sort(key=lambda x: (x[1] is None, -(x[1] if x[1] is not None else 0.0)))
    return result


def most_similar_player(tables: AgreementTables, player: str, rundle: str = None,
                         scope_rundle: str = None, scope_tier: str = None,
                         min_non_forfeit: int = 150, min_correct: int = 15):
    """
    The single other player with the highest pairwise agreement fraction
    with `player` in scope (default: the whole season) -- i.e. the top
    entry of one_pairwise_agreement. Returns ((rundle, player_name),
    fraction), or None if no other player in scope has a valid comparison.

    min_non_forfeit / min_correct: see one_pairwise_agreement.
    """
    matches = one_pairwise_agreement(
        tables, player, rundle=rundle,
        scope_rundle=scope_rundle, scope_tier=scope_tier,
        min_non_forfeit=min_non_forfeit, min_correct=min_correct,
    )
    return matches[0] if matches else None


def _top_pairs(tables: AgreementTables, idxs: np.ndarray, n: int, largest: bool) -> list:
    m = len(idxs)
    if m < 2:
        return []

    agree = tables.agree[np.ix_(idxs, idxs)].astype(np.float32)
    valid = tables.valid[np.ix_(idxs, idxs)].astype(np.float32)
    frac = np.full((m, m), np.nan, dtype=np.float32)
    np.divide(agree, valid, out=frac, where=valid > 0)

    # Exclude the diagonal and lower triangle (each pair counted once),
    # and pairs with no valid comparisons, by pushing them to the
    # "uninteresting" extreme so argpartition never selects them.
    fill = -np.inf if largest else np.inf
    rows = np.arange(m)
    frac[rows[:, None] >= rows[None, :]] = fill
    frac[np.isnan(frac)] = fill

    flat = frac.ravel()
    k = min(n, m * (m - 1) // 2)
    if k == 0:
        return []
    part = np.argpartition(flat, -k)[-k:] if largest else np.argpartition(flat, k - 1)[:k]

    pairs = []
    for f in part:
        val = float(flat[f])
        if not np.isfinite(val):
            continue
        r, c = divmod(int(f), m)
        pairs.append(((tables.labels[idxs[r]], tables.labels[idxs[c]]), val))
    pairs.sort(key=lambda x: x[1], reverse=largest)
    return pairs[:n]


def most_similar_pairs(tables: AgreementTables, n: int = 10, rundle: str = None, tier: str = None,
                        min_non_forfeit: int = 150, min_correct: int = 15) -> list:
    """
    The n most similar pairs of players in scope (default: whole season),
    as [((label1, label2), fraction), ...] sorted descending.

    min_non_forfeit: only consider players who themselves have at least
    this many non-forfeit answers (default: tables.num_questions, i.e. no
    forfeits at all) -- both players in a returned pair satisfy this.
    min_correct: only consider players who themselves answered at least
    this many questions correctly (default 15) -- filters out rows that
    are all-0 rather than genuinely forfeited, which would otherwise look
    spuriously "similar" to other low scorers.
    """
    idxs = _resolve_scope(tables, rundle, tier)
    idxs = _filter_players(tables, idxs, min_non_forfeit, min_correct)
    return _top_pairs(tables, idxs, n, largest=True)


def least_similar_pairs(tables: AgreementTables, n: int = 10, rundle: str = None, tier: str = None,
                         min_non_forfeit: int = 150, min_correct: int = 15) -> list:
    """
    The n least similar pairs of players in scope (default: whole season),
    as [((label1, label2), fraction), ...] sorted ascending.

    min_non_forfeit: only consider players who themselves have at least
    this many non-forfeit answers (default: tables.num_questions, i.e. no
    forfeits at all) -- both players in a returned pair satisfy this.
    min_correct: only consider players who themselves answered at least
    this many questions correctly (default 15) -- filters out rows that
    are all-0 rather than genuinely forfeited.
    """
    idxs = _resolve_scope(tables, rundle, tier)
    idxs = _filter_players(tables, idxs, min_non_forfeit, min_correct)
    return _top_pairs(tables, idxs, n, largest=False)


def agreement_dict_for_rundle(tables: AgreementTables, rundle: str) -> dict:
    """
    Build a { player_name: { player_name: fraction } } dict for a single
    rundle, in the shape ll_analysis.print_agreement_matrix expects.
    (Only sensible for a single rundle -- a season-wide dict of this shape
    would be ~36,000^2 python floats.)
    """
    idxs = _resolve_scope(tables, rundle=rundle)
    names = [tables.labels[i][1] for i in idxs]
    agree = tables.agree[np.ix_(idxs, idxs)]
    valid = tables.valid[np.ix_(idxs, idxs)]

    result = {p: {} for p in names}
    for a, name_a in enumerate(names):
        for b, name_b in enumerate(names):
            if a == b:
                continue
            v = int(valid[a, b])
            result[name_a][name_b] = (int(agree[a, b]) / v) if v > 0 else None
    return result


if __name__ == "__main__":
    SEASON = 109
    tables = get_tables(season=SEASON)
    print(f"LL{SEASON}: built agreement tables for {len(tables)} (rundle, player) rows.\n")

    # --- Whole-season leaders/laggards by average agreement ---
    # avgs = average_agreement(tables)
    # print("Top 10 average agreement (whole season):")
    # for label, avg in avgs[:10]:
    #     print(f"  {label[1]:<14} ({label[0]:<12}) {avg:.1%}" if avg is not None else f"  {label} n/a")

    # --- Example: single rundle, cheap and exact like ll_analysis.py ---
    example_rundle = tables.labels[0][0]
    print(f"\nAverage agreement within {example_rundle}:")
    for label, avg in average_agreement(tables, rundle=example_rundle):
        print(f"  {label[1]:<14} {avg:.1%}" if avg is not None else f"  {label[1]} n/a")

    # print(f"\nMost similar pairs within {example_rundle}:")
    # for (a, b), val in most_similar_pairs(tables, n=5, rundle=example_rundle):
    #     print(f"  {a[1]} & {b[1]}: {val:.1%}")

    print(most_similar_player(tables, "OzarowC", min_non_forfeit=126))