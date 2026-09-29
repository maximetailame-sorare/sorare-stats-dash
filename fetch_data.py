"""Collecte les stats in-game Sorare et écrit data.json (lu par index.html).

Les moyennes sont recalculées à partir des stats match par match
(playerGameScores + detailedScore), ce qui permet de les calculer par
compétition. Méthode, identique à averageStats de Sorare :
  - on garde les N derniers matchs joués (minutes > 0) dans le périmètre ;
  - minutes et stats « par match » : moyenne par match joué ;
  - toutes les autres stats : total / minutes jouées × 90 ;
  - score : moyenne des scores SO5 de ces matchs.
"""
import json
import os
import re
import sys
import time
import statistics
from datetime import datetime, timezone

import requests

SORARE_API = "https://api.sorare.com/graphql"
POSITIONS = ["Goalkeeper", "Defender", "Midfielder", "Forward"]
RANGES = [5, 10, 15]
GAMES_DEPTH = 15            # playerGameScores ne renvoie pas plus de 15 matchs, même avec clé API
INTL = "intl"               # périmètre « Matchs internationaux »
ALL = "all"                 # périmètre « Tous les matchs »

REQUEST_DELAY = float(os.environ.get("REQUEST_DELAY", "1.0"))
INITIAL_BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "50"))  # réduit automatiquement si complexité trop élevée
# Pour tester en local sur un sous-ensemble : ONLY_COMPS="ligue-1-fr,premier-league-gb-eng"
ONLY_COMPS = [c for c in os.environ.get("ONLY_COMPS", "").split(",") if c]
OUTPUT = os.environ.get("OUTPUT", "data.json")

# (clé API, libellé, catégorie). L'ordre est celui de l'affichage.
STATS = [
    ("mins_played", "Minutes jouées", "general"),
    ("fouls", "Fautes", "general"),
    ("was_fouled", "Fautes subies", "general"),
    ("yellow_card", "Carton jaune", "general"),
    ("error_lead_to_shot", "Erreurs menant à un tir", "general"),
    ("penalty_won", "Penaltys obtenus", "general"),
    ("penalty_kick_missed", "Penaltys manqués", "general"),
    ("goals_conceded", "Buts encaissés", "general"),
    ("won_tackle", "Tacles réussis", "defending"),
    ("effective_clearance", "Dégagements efficaces", "defending"),
    ("blocked_cross", "Centres contrés", "defending"),
    ("outfielder_block", "Tirs contrés", "defending"),
    ("clean_sheet_60", "Clean sheet (60 min)", "defending"),
    ("double_double", "Double-double", "defending"),
    ("triple_double", "Triple-double", "defending"),
    ("triple_triple", "Triple-triple", "defending"),
    ("duel_won", "Duels gagnés", "possession"),
    ("duel_lost", "Duels perdus", "possession"),
    ("interception_won", "Interceptions", "possession"),
    ("poss_won", "Ballons récupérés", "possession"),
    ("poss_lost_ctrl", "Pertes de balle", "possession"),
    ("accurate_pass", "Passes réussies", "passing"),
    ("missed_pass", "Passes ratées", "passing"),
    ("successful_final_third_passes", "Passes dernier tiers", "passing"),
    ("accurate_long_balls", "Longs ballons réussis", "passing"),
    ("long_pass_own_to_opp_success", "Longs ballons vers le camp adverse", "passing"),
    ("adjusted_total_att_assist", "Passes menant à un tir", "passing"),
    ("big_chance_created", "Grosses occasions créées", "passing"),
    ("ontarget_scoring_att", "Tirs cadrés", "attacking"),
    ("won_contest", "Dribbles réussis", "attacking"),
    ("pen_area_entries", "Entrées en surface", "attacking"),
    ("big_chance_missed", "Grosses occasions manquées", "attacking"),
    ("saves", "Arrêts", "goalkeeping"),
    ("saved_ibox", "Arrêts dans la surface", "goalkeeping"),
    ("dive_save", "Arrêts en plongeon", "goalkeeping"),
    ("dive_catch", "Arrêts en plongeon captés", "goalkeeping"),
    ("good_high_claim", "Sorties aériennes réussies", "goalkeeping"),
    ("punches", "Ballons boxés", "goalkeeping"),
    ("gk_smother", "Sorties dans les pieds", "goalkeeping"),
    ("accurate_keeper_sweeper", "Sorties hors surface réussies", "goalkeeping"),
    ("cross_not_claimed", "Centres non captés", "goalkeeping"),
    ("six_second_violation", "Règle des 6 secondes", "goalkeeping"),
    ("goals", "Buts", "decisive_pos"),
    ("goal_assist", "Passes décisives", "decisive_pos"),
    ("assist_penalty_won", "Penalty obtenu (assist)", "decisive_pos"),
    ("clearance_off_line", "Sauvetages sur la ligne", "decisive_pos"),
    ("last_man_tackle", "Tacle dernier homme", "decisive_pos"),
    ("penalty_save", "Penaltys arrêtés", "decisive_pos"),
    ("red_card", "Carton rouge", "decisive_neg"),
    ("own_goals", "CSC", "decisive_neg"),
    ("penalty_conceded", "Penaltys concédés", "decisive_neg"),
    ("error_lead_to_goal", "Erreurs menant au but", "decisive_neg"),
]
STAT_KEYS = [k for k, _, _ in STATS]
# Moyennées par match joué plutôt que ramenées à 90 minutes
PER_MATCH = {"mins_played", "clean_sheet_60", "double_double", "triple_double", "triple_triple"}
GK_STATS = {k for k, _, cat in STATS if cat == "goalkeeping"}

# Les slugs sont ceux de searchPlayers (active_competitions) ; ce sont aussi
# ceux de anyGame.competition.slug.
COMPETITIONS = {
    "premier-league-gb-eng": "Premier League",
    "football-league-championship": "EFL Championship",
    "bundesliga-de": "Bundesliga",
    "2-bundesliga": "2. Bundesliga",
    "laliga-es": "La Liga",
    "segunda-division-es": "La Liga 2",
    "ligue-1-fr": "Ligue 1",
    "ligue-2-fr": "Ligue 2",
    "k-league-1": "K League 1",
    "primeira-liga-pt": "Primeira Liga",
    "spor-toto-super-lig": "Süper Lig",
    "premiership-gb-sct": "Scottish Premiership",
    "austrian-bundesliga": "Austrian Bundesliga",
    "superliga-dk": "Danish Superliga",
    "superliga-argentina-de-futbol": "Superliga Argentina",
    "mlspa": "Major League Soccer",
    "j1-league": "J1 League",
    "uefa-champions-league": "Champions League",
    "uefa-europa-league": "Europa League",
    "uefa-europa-conference-league": "Europa Conference League",
    "eredivisie": "Eredivisie",
    "jupiler-pro-league": "Jupiler Pro League",
    "serie-a-it": "Serie A",
}

CHALLENGER_SLUGS = {
    "primeira-liga-pt", "spor-toto-super-lig", "premiership-gb-sct",
    "austrian-bundesliga", "superliga-dk", "serie-a-it",
}

CONTENDER_SLUGS = {
    "ligue-2-fr", "2-bundesliga", "segunda-division-es",
    "superliga-argentina-de-futbol", "football-league-championship",
}


def build_headers():
    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get("SORARE_API_KEY")
    if api_key:
        headers["APIKEY"] = api_key
    return headers


def post(query, timeout=60, retries=8):
    """POST GraphQL. Renvoie (data, error_message)."""
    for attempt in range(retries):
        try:
            r = requests.post(SORARE_API, json={"query": query}, headers=build_headers(), timeout=timeout)
            if r.status_code == 429:
                retry_after = r.headers.get("Retry-After", "")
                wait = int(retry_after) + 1 if retry_after.isdigit() else 10 * (attempt + 1)
                print(f"  429 rate limit — waiting {wait}s...", file=sys.stderr, flush=True)
                time.sleep(wait)
                continue
            if r.status_code == 422:
                return None, r.text[:400]
            r.raise_for_status()
            body = r.json()
            if "errors" in body:
                return body.get("data"), body["errors"][0].get("message", "UNKNOWN_ERROR")
            return body.get("data"), None
        except Exception as exc:
            if attempt == retries - 1:
                return None, f"Request failed: {exc}"
            time.sleep(2 ** attempt)
    return None, "Too many retries"


def get_player_slugs(comp_slug, position):
    """Liste les joueurs d'une compétition et d'un poste. Pagine jusqu'au bout."""
    results = []
    page = 1
    while True:
        query = f"""
        {{
          searchPlayers(
            advancedFilters: "sport:football AND active_competitions:{comp_slug} AND position:{position}",
            pageSize: 100,
            page: {page}
          ) {{
            hits {{
              player {{
                slug
                displayName
                age
                activeClub {{ name }}
                activeNationalTeam {{ name country {{ code }} }}
              }}
            }}
          }}
        }}
        """
        data, err = post(query, timeout=30)
        if err:
            print(f"  GQL error: {err}", file=sys.stderr, flush=True)
        hits = ((data or {}).get("searchPlayers") or {}).get("hits") or []
        for h in hits:
            p = h.get("player") or {}
            if p.get("slug"):
                nat = p.get("activeNationalTeam") or {}
                results.append({
                    "slug": p["slug"],
                    "name": p.get("displayName") or p["slug"],
                    "club": (p.get("activeClub") or {}).get("name", ""),
                    "age": p.get("age"),
                    "nat": nat.get("name"),
                    "nat_code": (nat.get("country") or {}).get("code"),
                })
        if len(hits) < 100:
            break
        page += 1
        time.sleep(REQUEST_DELAY)
    return results


GAMES_QUERY = """{{
  players(slugs: [{slugs}]) {{
    slug
    ... on Player {{
      playerGameScores(last: {depth}) {{
        score
        anyGame {{ date competition {{ slug }} ... on Game {{ homeTeam {{ __typename }} }} }}
        detailedScore {{ stat statValue }}
      }}
    }}
  }}
}}"""

_batch_size = INITIAL_BATCH_SIZE


def fetch_games(slugs):
    """Renvoie {slug: [match, ...]} ; un match = {date, comp, intl, score, stats}.
    Réduit la taille des lots quand l'API signale une complexité trop élevée."""
    global _batch_size
    if not slugs:
        return {}
    query = GAMES_QUERY.format(slugs=", ".join(f'"{s}"' for s in slugs), depth=GAMES_DEPTH)
    data, err = post(query)
    if err and "complexity" in err.lower() and len(slugs) > 1:
        m = re.search(r"complexity of (\d+).*?max complexity of (\d+)", err)
        if m:
            cur, mx = int(m.group(1)), int(m.group(2))
            new_size = max(1, int(len(slugs) * mx / cur * 0.9))
        else:
            new_size = max(1, len(slugs) // 2)
        if new_size < _batch_size:
            print(f"  Complexity — batch size {_batch_size} → {new_size}", flush=True)
            _batch_size = new_size
        out = {}
        for i in range(0, len(slugs), new_size):
            out.update(fetch_games(slugs[i:i + new_size]))
            time.sleep(REQUEST_DELAY)
        return out
    if err:
        print(f"  GQL error: {err[:200]}", file=sys.stderr, flush=True)
    result = {}
    for p in (data or {}).get("players") or []:
        if not p or not p.get("slug"):
            continue
        games = []
        for g in p.get("playerGameScores") or []:
            game = g.get("anyGame") or {}
            stats = {d["stat"]: d["statValue"] for d in (g.get("detailedScore") or []) if d.get("stat")}
            games.append({
                "date": game.get("date") or "",
                "comp": (game.get("competition") or {}).get("slug"),
                "intl": (game.get("homeTeam") or {}).get("__typename") == "NationalTeam",
                "score": g.get("score"),
                "stats": stats,
            })
        games.sort(key=lambda x: x["date"], reverse=True)
        result[p["slug"]] = games
    return result


def r1(v):
    """Arrondi compact pour le JSON."""
    v = round(v, 2)
    return int(v) if v == int(v) else v


def compute_block(games, n, position):
    """Moyennes sur les n derniers matchs joués de la liste (déjà triée, récents d'abord).
    Renvoie [nb_matchs, score, stat1, stat2, ...] ou None."""
    played = [g for g in games if (g["stats"].get("mins_played") or 0) > 0][:n]
    if not played:
        return None
    mins = sum(g["stats"]["mins_played"] for g in played)
    scores = [g["score"] for g in played if g["score"] is not None]
    block = [len(played), r1(sum(scores) / len(scores)) if scores else None]
    for k in STAT_KEYS:
        # L'API renvoie les stats gardien (à 0) pour tous les postes : on ne les garde que pour les gardiens
        if k in GK_STATS and position != "Goalkeeper":
            block.append(None)
            continue
        present = [g["stats"][k] for g in played if k in g["stats"]]
        if not present:          # stat absente du barème de ce joueur (ex. arrêts pour un attaquant)
            block.append(None)
            continue
        total = sum(present)
        block.append(r1(total / len(played)) if k in PER_MATCH else r1(total / mins * 90))
    return block


def main():
    competitions = {k: v for k, v in COMPETITIONS.items() if not ONLY_COMPS or k in ONLY_COMPS}
    print(f"Using {len(competitions)} competitions", flush=True)

    # Étape 1 : liste des joueurs par (compétition, poste)
    player_meta = {}
    for comp_slug, comp_name in competitions.items():
        for position in POSITIONS:
            found = get_player_slugs(comp_slug, position)
            print(f"Listing {position}s — {comp_name}: {len(found)}", flush=True)
            for p in found:
                meta = player_meta.setdefault(p["slug"], {**p, "position": position, "comps": []})
                if comp_slug not in meta["comps"]:
                    meta["comps"].append(comp_slug)
            time.sleep(REQUEST_DELAY)

    all_slugs = list(player_meta.keys())
    total = len(all_slugs)

    # Étape 2 : matchs détaillés
    print(f"\nFetching games for {total} unique players...", flush=True)
    player_games = {}
    i = 0
    while i < total:
        batch = all_slugs[i: i + _batch_size]
        print(f"  {i}/{total} (batch {len(batch)})...", flush=True)
        player_games.update(fetch_games(batch))
        i += len(batch)
        time.sleep(REQUEST_DELAY)

    depths = [len(g) for g in player_games.values()]
    depth_info = {
        "requested": GAMES_DEPTH,
        "max": max(depths) if depths else 0,
        "median": statistics.median(depths) if depths else 0,
    }
    print(f"\nGames per player: {depth_info}", flush=True)

    # Étape 3 : une ligne par (joueur, périmètre)
    players_out, rows = [], []
    for slug, meta in player_meta.items():
        games = player_games.get(slug)
        if not games:
            continue
        idx = len(players_out)
        players_out.append([slug, meta["name"], meta["club"], meta["age"], meta["position"],
                            meta["nat"], meta["nat_code"]])
        scopes = {ALL: games}
        for c in meta["comps"]:
            scopes[c] = [g for g in games if g["comp"] == c]
        intl = [g for g in games if g["intl"]]
        if intl:
            scopes[INTL] = intl
        for scope, gs in scopes.items():
            blocks = []
            prev = None
            for n in RANGES:
                b = compute_block(gs, n, meta["position"])
                # 0 = identique à la range précédente (joueur avec moins de n matchs) : allège le fichier
                blocks.append(0 if (b is not None and prev is not None and b == prev) else b)
                prev = b
            if blocks[0] is None:
                continue
            rows.append([idx, scope, *blocks])

    comps_out = [[slug, name, "contender" if slug in CONTENDER_SLUGS else
                  "challenger" if slug in CHALLENGER_SLUGS else ""]
                 for slug, name in competitions.items()]
    print(f"Done — {len(players_out)} players, {len(rows)} rows", flush=True)
    return {
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "depth": depth_info,
        "ranges": RANGES,
        "stats": [[k, label, cat, "match" if k in PER_MATCH else "90"] for k, label, cat in STATS],
        "comps": comps_out,
        "players": players_out,
        "rows": rows,
    }


if __name__ == "__main__":
    out = main()
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"→ {OUTPUT} ({os.path.getsize(OUTPUT) / 1e6:.1f} Mo)", flush=True)
