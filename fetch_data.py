import json
import os
import sys
import time
import requests
from datetime import datetime

SORARE_API = "https://api.sorare.com/graphql"
POSITIONS = ["Goalkeeper", "Defender", "Midfielder", "Forward"]
RANGES = [5, 10, 40]
RANGE_LIMITS = {5: "LAST_5", 10: "LAST_10", 40: "LAST_40"}
REQUEST_DELAY = 1.5
INITIAL_BATCH_SIZE = 5  # auto-splits on complexity error

STAT_TYPES = [
    "ACCURATE_PASS", "ASSIST_PENALTY_WON", "BIG_CHANCE_CREATED", "CLEARANCE_OFF_LINE",
    "DUEL_WON", "EFFECTIVE_CLEARANCE", "FOULS", "GOALS",
    "INTERCEPTION_WON", "LOST_CORNERS", "MINS_PLAYED",
    "ONTARGET_SCORING_ATT", "OWN_GOALS", "PENALTY_SAVE",
    "PENALTY_WON", "RED_CARD", "SAVES", "WAS_FOULED", "WON_CONTEST", "WON_TACKLE",
    "YELLOW_CARD",
]

STAT_LABELS = {
    "score": "Score SO5",
    "ACCURATE_PASS": "Passes réussies",
    "ASSIST_PENALTY_WON": "Assist penalty obtenu",
    "BIG_CHANCE_CREATED": "Grosses occasions créées",
    "CLEARANCE_OFF_LINE": "Dégagements sur la ligne",
    "DUEL_WON": "Duels gagnés",
    "EFFECTIVE_CLEARANCE": "Dégagements efficaces",
    "ERROR_LEAD_TO_GOAL": "Erreurs menant au but",
    "FOULS": "Fautes",
    "GOALS": "Buts",
    "INTERCEPTION_WON": "Interceptions",
    "LAST_MAN_TACKLE": "Tacle dernier homme",
    "LOST_CORNERS": "Corners perdus",
    "MINS_PLAYED": "Minutes jouées",
    "ONTARGET_SCORING_ATT": "Tirs cadrés",
    "OWN_GOALS": "CSC",
    "PENALTY_CONCEDED": "Penaltys concédés",
    "PENALTY_SAVE": "Penaltys arrêtés",
    "PENALTY_WON": "Penaltys obtenus",
    "RED_CARD": "Carton rouge",
    "SAVES": "Arrêts",
    "WAS_FOULED": "Fautes subies",
    "WON_CONTEST": "Dribbles",
    "WON_TACKLE": "Tacles réussis",
    "YELLOW_CARD": "Carton jaune",
}

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
    "primera-liga-pt": "Primeira Liga",
    "spor-toto-super-lig": "Süper Lig",
    "premiership-gb-sct": "Scottish Premiership",
    "austrian-bundesliga": "Austrian Bundesliga",
    "superliga-dk": "Danish Superliga",
    "superliga-argentina-de-futbol": "Superliga Argentina",
    "mlspa": "Major League Soccer",
    "j1-100-year-vision-league": "J1 League",
    "uefa-champions-league": "Champions League",
    "uefa-europa-league": "Europa League",
    "uefa-europa-conference-league": "Europa Conference League",
    "eredivisie-nl": "Eredivisie",
    "jupiler-pro-league": "Jupiler Pro League",
    "serie-a-it": "Serie A",
}

CHALLENGER_SLUGS = {
    "primera-liga-pt", "spor-toto-super-lig", "premiership-gb-sct",
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


def gql(query, retries=5):
    for attempt in range(retries):
        try:
            r = requests.post(SORARE_API, json={"query": query}, headers=build_headers(), timeout=30)
            if r.status_code == 429:
                wait = 10 * (attempt + 1)
                print(f"  429 rate limit — waiting {wait}s...", file=sys.stderr, flush=True)
                time.sleep(wait)
                continue
            r.raise_for_status()
            body = r.json()
            if "errors" in body:
                msg = body["errors"][0].get("message", "")
                print(f"  GQL error: {msg}", file=sys.stderr, flush=True)
                return None
            return body.get("data")
        except Exception as exc:
            if attempt == retries - 1:
                print(f"  Request failed: {exc}", file=sys.stderr, flush=True)
                return None
            time.sleep(2 ** attempt)
    return None




def _stat_aliases():
    """Generate all averageStats aliases: r{n}_{TYPE} for each range × stat type."""
    lines = []
    for n, limit in RANGE_LIMITS.items():
        for t in STAT_TYPES:
            lines.append(f"r{n}_{t}: averageStats(limit: {limit}, type: {t})")
    # Also fetch the overall SO5 score average
    for n, limit in RANGE_LIMITS.items():
        lines.append(f"r{n}_score: averageStats(limit: {limit}, type: WON_CONTEST)")
    return "\n".join(lines)


STAT_ALIASES = _stat_aliases()


def _parse_player_stats(p):
    """Extract stats dict {range: {type: value}} from a player GQL response."""
    stats = {}
    for n in RANGES:
        s = {}
        for t in STAT_TYPES + ["score"]:
            key = f"r{n}_{t}"
            v = p.get(key)
            if v is not None:
                s[t] = round(float(v), 2)
        stats[str(n)] = s if s else None
    return stats


def get_player_slugs(comp_slug, position):
    """Lightweight query — just slugs, name, club. Paginates until exhausted."""
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
                activeClub {{ name }}
              }}
            }}
          }}
        }}
        """
        data = gql(query)
        hits = (data or {}).get("searchPlayers", {}).get("hits") or []
        for h in hits:
            p = h.get("player") or {}
            if p.get("slug"):
                results.append({
                    "slug": p["slug"],
                    "name": p.get("displayName") or p["slug"],
                    "club": (p.get("activeClub") or {}).get("name", ""),
                })
        if len(hits) < 100:
            break
        page += 1
        time.sleep(REQUEST_DELAY)
    return results


def fetch_stats_batch(slugs):
    """Fetch averageStats for a batch of players. Auto-splits on complexity error."""
    if not slugs:
        return {}
    player_blocks = "\n".join(
        f'p{i}: player(slug: "{slug}") {{ {STAT_ALIASES} }}'
        for i, slug in enumerate(slugs)
    )
    query = f"{{ football {{ {player_blocks} }} }}"

    for attempt in range(5):
        try:
            r = requests.post(SORARE_API, json={"query": query}, headers=build_headers(), timeout=60)
            if r.status_code == 429:
                wait = 10 * (attempt + 1)
                print(f"  429 — waiting {wait}s...", flush=True)
                time.sleep(wait)
                continue
            r.raise_for_status()
            body = r.json()
            if "errors" in body:
                msg = body["errors"][0].get("message", "")
                if "complexity" in msg.lower() and len(slugs) > 1:
                    mid = len(slugs) // 2
                    print(f"  Complexity — splitting {len(slugs)} → {mid}+{len(slugs)-mid}", flush=True)
                    return {**fetch_stats_batch(slugs[:mid]), **fetch_stats_batch(slugs[mid:])}
                print(f"  GQL error: {msg}", file=sys.stderr, flush=True)
                return {}
            football = (body.get("data") or {}).get("football") or {}
            return {
                slugs[i]: _parse_player_stats(football.get(f"p{i}") or {})
                for i in range(len(slugs))
            }
        except Exception as exc:
            if attempt == 4:
                print(f"  Batch failed: {exc}", file=sys.stderr, flush=True)
                return {}
            time.sleep(2 ** attempt)
    return {}


def main():
    competitions = COMPETITIONS
    print(f"Using {len(competitions)} competitions", flush=True)

    # Step 1: collect all player metadata per (comp, position)
    player_meta = {}  # slug -> {name, club, comps: [...]}
    for comp_slug, comp_name in competitions.items():
        for position in POSITIONS:
            print(f"Listing {position}s — {comp_name}...", flush=True)
            for p in get_player_slugs(comp_slug, position):
                slug = p["slug"]
                if slug not in player_meta:
                    player_meta[slug] = {"name": p["name"], "club": p["club"], "comps": []}
                player_meta[slug]["comps"].append({
                    "comp_slug": comp_slug, "comp_name": comp_name, "position": position,
                })
            time.sleep(REQUEST_DELAY)

    # Step 2: fetch stats for all unique players
    all_slugs = list(player_meta.keys())
    total = len(all_slugs)
    print(f"\nFetching stats for {total} unique players (batch {INITIAL_BATCH_SIZE})...", flush=True)
    player_stats = {}
    for i in range(0, total, INITIAL_BATCH_SIZE):
        batch = all_slugs[i: i + INITIAL_BATCH_SIZE]
        print(f"  {i}/{total}...", flush=True)
        player_stats.update(fetch_stats_batch(batch))
        time.sleep(REQUEST_DELAY)

    # Step 3: build final list (one entry per player × comp × position)
    all_players = []
    for slug, meta in player_meta.items():
        stats = player_stats.get(slug, {})
        for c in meta["comps"]:
            all_players.append({
                "slug": slug,
                "name": meta["name"],
                "club": meta["club"],
                "position": c["position"],
                "comp_slug": c["comp_slug"],
                "comp_name": c["comp_name"],
                "stats": {str(n): stats.get(str(n)) for n in RANGES},
            })

    print(f"\nDone — {len(all_players)} entries, {total} unique players", flush=True)
    return all_players, competitions


def generate_html(players, competitions, last_updated):
    data_json = json.dumps({
        "last_updated": last_updated,
        "players": players,
        "stat_labels": STAT_LABELS,
    }, ensure_ascii=False)

    def group_values(slugs):
        return " ".join(slug for slug in competitions if slug in slugs)

    grouped_slugs = CHALLENGER_SLUGS | CONTENDER_SLUGS
    regular = sorted(
        ((slug, name) for slug, name in competitions.items() if slug not in grouped_slugs),
        key=lambda x: x[1]
    )
    challenger = sorted(
        ((slug, name) for slug, name in competitions.items() if slug in CHALLENGER_SLUGS),
        key=lambda x: x[1]
    )
    contender = sorted(
        ((slug, name) for slug, name in competitions.items() if slug in CONTENDER_SLUGS),
        key=lambda x: x[1]
    )

    def items_html(items, cls=""):
        return "\n".join(
            f'<label class="multi-option {cls}"><input type="checkbox" value="{slug}"> {name}</label>'
            for slug, name in items
        )

    comp_checkboxes = f"""
{items_html(regular)}
<label class="multi-option group-header"><input type="checkbox" id="comp-contender" data-group="{group_values(CONTENDER_SLUGS)}"> Contender</label>
{items_html(contender, "sub-option")}
<label class="multi-option group-header"><input type="checkbox" id="comp-challenger" data-group="{group_values(CHALLENGER_SLUGS)}"> Challenger</label>
{items_html(challenger, "sub-option")}
"""

    all_stats = ["score"] + STAT_TYPES
    stat_labels_js = json.dumps(STAT_LABELS)

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Sorare Stats Dashboard</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh}}
    header{{background:#1e293b;border-bottom:1px solid #334155;padding:18px 28px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px}}
    header h1{{font-size:1.3rem;font-weight:700;color:#f1f5f9}}
    #last-updated{{font-size:0.75rem;color:#64748b}}
    .tabs{{display:flex;gap:0;border-bottom:1px solid #334155;background:#1e293b;padding:0 28px}}
    .tab{{padding:12px 20px;cursor:pointer;font-size:0.85rem;font-weight:600;color:#64748b;border-bottom:2px solid transparent;transition:all .15s}}
    .tab.active{{color:#818cf8;border-bottom-color:#818cf8}}
    .tab-content{{display:none}}.tab-content.active{{display:block}}
    .controls{{padding:20px 28px 0;display:flex;flex-direction:column;gap:12px}}
    .filter-group{{display:flex;align-items:center;gap:8px;flex-wrap:wrap}}
    .filter-label{{font-size:0.72rem;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:.06em;min-width:90px}}
    .filter-btn{{background:#1e293b;border:1px solid #334155;color:#94a3b8;padding:5px 12px;border-radius:5px;cursor:pointer;font-size:0.8rem;transition:all .15s}}
    .filter-btn:hover{{border-color:#6366f1;color:#a5b4fc}}
    .filter-btn.active{{background:#6366f1;border-color:#6366f1;color:#fff;font-weight:600}}
    .range-btn{{border-radius:5px}}
    select.filter-select{{background:#1e293b;border:1px solid #334155;color:#e2e8f0;padding:5px 10px;border-radius:5px;font-size:0.82rem;cursor:pointer;outline:none}}
    select.filter-select:focus{{border-color:#6366f1}}
    .multi-wrap{{position:relative;display:inline-block}}
    .multi-trigger{{background:#1e293b;border:1px solid #334155;color:#e2e8f0;padding:5px 28px 5px 10px;border-radius:5px;font-size:0.82rem;cursor:pointer;user-select:none;min-width:140px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
    .multi-trigger::after{{content:'▾';position:absolute;right:9px;top:50%;transform:translateY(-50%);color:#64748b;pointer-events:none}}
    .multi-trigger.has-selection{{border-color:#6366f1;color:#a5b4fc}}
    .multi-dropdown{{display:none;position:absolute;top:calc(100% + 4px);left:0;background:#1e293b;border:1px solid #334155;border-radius:6px;z-index:100;min-width:200px;max-height:280px;overflow-y:auto;box-shadow:0 8px 24px #0008}}
    .multi-wrap.open .multi-dropdown{{display:block}}
    .multi-option{{display:flex;align-items:center;gap:8px;padding:7px 12px;cursor:pointer;font-size:0.82rem;color:#cbd5e1;transition:background .1s}}
    .multi-option:hover{{background:#334155}}
    .multi-option input[type=checkbox]{{accent-color:#6366f1;cursor:pointer}}
    .multi-option.all-opt{{border-bottom:1px solid #334155;color:#94a3b8;font-weight:600}}
    .multi-option.group-header{{border-top:1px solid #334155;color:#94a3b8;font-weight:600;margin-top:2px}}
    .multi-option.sub-option{{padding-left:24px;color:#94a3b8}}
    .slider-group{{display:flex;align-items:center;gap:12px;flex-wrap:wrap}}
    .slider-group input[type=range]{{-webkit-appearance:none;width:200px;height:4px;border-radius:2px;background:#334155;outline:none}}
    .slider-group input[type=range]::-webkit-slider-thumb{{-webkit-appearance:none;width:14px;height:14px;border-radius:50%;background:#6366f1;cursor:pointer}}
    .slider-val{{font-size:0.82rem;color:#e2e8f0;min-width:90px}}
    .search-wrap{{padding:16px 28px 0}}
    #player-search{{background:#1e293b;border:1px solid #334155;color:#e2e8f0;padding:8px 14px;border-radius:6px;font-size:0.9rem;width:320px}}
    #player-search:focus{{outline:none;border-color:#6366f1}}
    .aggregate-box{{margin:16px 28px 0;background:#1e293b;border:1px solid #334155;border-radius:8px;padding:16px 20px}}
    .aggregate-box h3{{font-size:0.8rem;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:.05em;margin-bottom:12px}}
    .agg-stats{{display:flex;flex-wrap:wrap;gap:10px}}
    .agg-stat{{background:#0f172a;border-radius:6px;padding:8px 12px;min-width:100px}}
    .agg-stat .label{{font-size:0.68rem;color:#64748b;text-transform:uppercase;letter-spacing:.04em}}
    .agg-stat .value{{font-size:1.1rem;font-weight:700;color:#e2e8f0;margin-top:2px}}
    .agg-stat .value.high{{color:#4ade80}}.agg-stat .value.mid{{color:#facc15}}.agg-stat .value.low{{color:#f87171}}
    #group-count{{padding:12px 28px 0;font-size:0.85rem;color:#94a3b8;font-style:italic}}
    #player-count{{padding:8px 28px 0;font-size:0.78rem;color:#64748b}}
    .table-wrap{{padding:16px 28px 28px;overflow-x:auto}}
    table{{width:100%;border-collapse:collapse;font-size:0.82rem}}
    thead tr{{background:#1e293b}}
    th{{padding:9px 12px;text-align:left;color:#94a3b8;font-weight:600;font-size:0.7rem;text-transform:uppercase;letter-spacing:.05em;white-space:nowrap;cursor:pointer;user-select:none}}
    th:hover{{color:#c7d2fe}}
    th .arr{{margin-left:3px;opacity:.4}}
    th.sorted .arr{{opacity:1;color:#818cf8}}
    td{{padding:8px 12px;border-bottom:1px solid #1e293b;white-space:nowrap}}
    tr:hover td{{background:#1e293b66}}
    .badge{{display:inline-block;padding:2px 7px;border-radius:4px;font-size:0.68rem;font-weight:700}}
    .pos-Goalkeeper{{background:#164e63;color:#67e8f9}}
    .pos-Defender{{background:#14532d;color:#86efac}}
    .pos-Midfielder{{background:#312e81;color:#a5b4fc}}
    .pos-Forward{{background:#7f1d1d;color:#fca5a5}}
    .score-val{{font-weight:700}}
    .high{{color:#4ade80}}.mid{{color:#facc15}}.low{{color:#f87171}}
    .no-data{{text-align:center;padding:60px;color:#475569}}
    .player-card{{margin:16px 28px;background:#1e293b;border-radius:8px;padding:20px}}
    .player-card h2{{font-size:1.1rem;font-weight:700;color:#f1f5f9;margin-bottom:4px}}
    .player-card .meta{{font-size:0.8rem;color:#64748b;margin-bottom:16px}}
    .range-tabs{{display:flex;gap:6px;margin-bottom:16px}}
    .range-tab{{padding:5px 14px;border-radius:5px;cursor:pointer;font-size:0.8rem;font-weight:600;background:#0f172a;border:1px solid #334155;color:#64748b;transition:all .15s}}
    .range-tab.active{{background:#6366f1;border-color:#6366f1;color:#fff}}
    .stats-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:8px}}
    .stat-card{{background:#0f172a;border-radius:6px;padding:10px 12px}}
    .stat-card .slabel{{font-size:0.68rem;color:#64748b;text-transform:uppercase;letter-spacing:.04em}}
    .stat-card .sval{{font-size:1.15rem;font-weight:700;color:#e2e8f0;margin-top:3px}}
    .empty-state{{text-align:center;padding:60px 28px;color:#475569}}
  </style>
</head>
<body>

<header>
  <h1>Sorare Stats Dashboard</h1>
  <span id="last-updated"></span>
</header>

<div class="tabs">
  <div class="tab active" data-tab="group">Vue groupe</div>
  <div class="tab" data-tab="player">Vue joueur</div>
</div>

<!-- GROUP VIEW -->
<div id="tab-group" class="tab-content active">
  <div class="controls">
    <div class="filter-group">
      <span class="filter-label">Range</span>
      <select id="range-select" class="filter-select">
        <option value="10">10 matchs</option>
        <option value="5">5 matchs</option>
        <option value="40">40 matchs</option>
      </select>
    </div>
    <div class="filter-group">
      <span class="filter-label">Poste</span>
      <div class="multi-wrap" id="pos-wrap">
        <div class="multi-trigger" id="pos-trigger">Tous</div>
        <div class="multi-dropdown">
          <label class="multi-option all-opt"><input type="checkbox" id="pos-all" checked> Tous</label>
          <label class="multi-option"><input type="checkbox" value="Goalkeeper"> Gardien</label>
          <label class="multi-option"><input type="checkbox" value="Defender"> Défenseur</label>
          <label class="multi-option"><input type="checkbox" value="Midfielder"> Milieu</label>
          <label class="multi-option"><input type="checkbox" value="Forward"> Attaquant</label>
        </div>
      </div>
    </div>
    <div class="filter-group">
      <span class="filter-label">Championnat</span>
      <div class="multi-wrap" id="comp-wrap">
        <div class="multi-trigger" id="comp-trigger">Tous</div>
        <div class="multi-dropdown">
          <label class="multi-option all-opt"><input type="checkbox" id="comp-all" checked> Tous</label>
          {comp_checkboxes}
        </div>
      </div>
    </div>
    <div class="filter-group" id="club-filter-group" style="display:none">
      <span class="filter-label">Club</span>
      <div class="multi-wrap" id="club-wrap">
        <div class="multi-trigger" id="club-trigger">Tous</div>
        <div class="multi-dropdown" id="club-dropdown">
          <label class="multi-option all-opt"><input type="checkbox" id="club-all" checked> Tous</label>
        </div>
      </div>
    </div>
    <div class="filter-group slider-group">
      <span class="filter-label">Min. jouées</span>
      <input type="range" id="mins-slider" min="0" max="90" value="0" step="5"/>
      <span class="slider-val" id="mins-val">≥ 0 min</span>
    </div>
    <div class="filter-group">
      <span class="filter-label">Agrégation</span>
      <button class="filter-btn agg-btn active" data-agg="mean">Moyenne</button>
      <button class="filter-btn agg-btn" data-agg="median">Médiane</button>
      <button class="filter-btn agg-btn" data-agg="top10">Top 10%</button>
      <button class="filter-btn agg-btn" data-agg="top20">Top 20%</button>
    </div>
  </div>

  <div id="group-count"></div>

  <div class="aggregate-box" id="agg-box">
    <h3 id="agg-title">Moyenne du groupe</h3>
    <div class="agg-stats" id="agg-stats"></div>
  </div>

  <div class="table-wrap">
    <table>
      <thead id="group-thead"></thead>
      <tbody id="group-tbody"></tbody>
    </table>
    <div id="group-no-data" class="no-data" style="display:none">Aucun joueur trouvé</div>
  </div>
</div>

<!-- PLAYER VIEW -->
<div id="tab-player" class="tab-content">
  <div class="search-wrap">
    <input id="player-search" type="text" placeholder="Rechercher un joueur par nom…"/>
  </div>
  <div id="player-count"></div>
  <div id="player-results"></div>
</div>

<script>
const DATA = {data_json};
const STAT_LABELS = {stat_labels_js};
const ALL_STATS = ["score", {', '.join(f'"{f}"' for f in STAT_TYPES)}];
const POS_LABELS = {{Goalkeeper:'Gardien',Defender:'Défenseur',Midfielder:'Milieu',Forward:'Attaquant'}};

document.getElementById('last-updated').textContent = 'Mis à jour : ' + DATA.last_updated;

// ── State ──────────────────────────────────────────────────
let activeTab = 'group';
let activeRange = 10;
let selectedPos = new Set();   // empty = tous
let selectedComp = new Set();  // empty = tous
let selectedClub = new Set();  // empty = tous
let minMins = 0;
const CLUB_FILTER_COMPS = new Set(["premier-league-gb-eng","bundesliga-de","laliga-es","ligue-1-fr"]);
let aggMode = 'mean';
let sortCol = 'score';
let sortDir = -1;
let playerSearch = '';
let playerSortRange = 10;

// ── Tab switching ─────────────────────────────────────────
document.querySelectorAll('.tab').forEach(t => {{
  t.addEventListener('click', () => {{
    activeTab = t.dataset.tab;
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    document.querySelectorAll('.tab-content').forEach(x => x.classList.remove('active'));
    document.getElementById('tab-' + activeTab).classList.add('active');
    if (activeTab === 'group') renderGroup();
    else renderPlayer();
  }});
}});

// ── Helpers ───────────────────────────────────────────────
function fmt(v) {{ return (v === null || v === undefined) ? '—' : v; }}

function scoreClass(v) {{
  if (v === null || v === undefined) return '';
  if (v >= 60) return 'high'; if (v >= 40) return 'mid'; return 'low';
}}

function avg(arr) {{
  const vals = arr.filter(v => v !== null && v !== undefined);
  if (!vals.length) return null;
  return Math.round((vals.reduce((a,b)=>a+b,0)/vals.length)*100)/100;
}}

function median(arr) {{
  const vals = arr.filter(v => v !== null && v !== undefined).sort((a,b)=>a-b);
  if (!vals.length) return null;
  const m = Math.floor(vals.length/2);
  const r = vals.length%2 ? vals[m] : (vals[m-1]+vals[m])/2;
  return Math.round(r*100)/100;
}}

function topPct(arr, pct) {{
  const vals = arr.filter(v => v !== null && v !== undefined).sort((a,b)=>b-a);
  if (!vals.length) return null;
  const n = Math.max(1, Math.round(vals.length * pct));
  const top = vals.slice(0, n);
  return Math.round((top.reduce((a,b)=>a+b,0)/top.length)*100)/100;
}}

function aggregate(arr) {{
  if (aggMode === 'median') return median(arr);
  if (aggMode === 'top10') return topPct(arr, 0.10);
  if (aggMode === 'top20') return topPct(arr, 0.20);
  return avg(arr);
}}

const AGG_LABELS = {{ mean:'Moyenne du groupe', median:'Médiane du groupe', top10:'Top 10% du groupe', top20:'Top 20% du groupe' }};

// ── Group view ────────────────────────────────────────────
function filteredPlayers() {{
  return DATA.players.filter(p => {{
    if (selectedPos.size > 0 && !selectedPos.has(p.position)) return false;
    if (selectedComp.size > 0 && !selectedComp.has(p.comp_slug)) return false;
    if (selectedClub.size > 0 && !selectedClub.has(p.club)) return false;
    if (minMins > 0) {{
      const s = (p.stats && p.stats[String(activeRange)]) || {{}};
      if ((s.MINS_PLAYED || 0) < minMins) return false;
    }}
    return true;
  }});
}}

// Club dropdown — trigger & close listeners attached once
const clubWrap = document.getElementById('club-wrap');
const clubTrigger = document.getElementById('club-trigger');
clubTrigger.addEventListener('click', e => {{ e.stopPropagation(); clubWrap.classList.toggle('open'); }});
document.addEventListener('click', e => {{ if (!clubWrap.contains(e.target)) clubWrap.classList.remove('open'); }});

function updateClubFilter() {{
  const relevantComps = (selectedComp.size > 0 ? [...selectedComp] : [...CLUB_FILTER_COMPS])
    .filter(c => CLUB_FILTER_COMPS.has(c));
  const group = document.getElementById('club-filter-group');

  if (relevantComps.length === 0) {{
    group.style.display = 'none';
    selectedClub.clear();
    clubTrigger.textContent = 'Tous';
    clubTrigger.classList.remove('has-selection');
    return;
  }}
  group.style.display = '';

  const clubs = [...new Set(
    DATA.players.filter(p => relevantComps.includes(p.comp_slug)).map(p => p.club)
  )].sort();

  const dropdown = document.getElementById('club-dropdown');
  dropdown.innerHTML =
    `<label class="multi-option all-opt"><input type="checkbox" id="club-all" checked> Tous</label>` +
    clubs.map(c => `<label class="multi-option"><input type="checkbox" value="${{c}}"> ${{c}}</label>`).join('');

  selectedClub.clear();
  clubTrigger.textContent = 'Tous';
  clubTrigger.classList.remove('has-selection');

  function refreshClubTrigger() {{
    if (selectedClub.size === 0) {{
      clubTrigger.textContent = 'Tous'; clubTrigger.classList.remove('has-selection');
    }} else {{
      clubTrigger.textContent = selectedClub.size <= 2 ? [...selectedClub].join(', ') : selectedClub.size + ' sélectionnés';
      clubTrigger.classList.add('has-selection');
    }}
  }}

  const allCb = document.getElementById('club-all');
  allCb.addEventListener('change', () => {{
    if (allCb.checked) {{
      selectedClub.clear();
      dropdown.querySelectorAll('input[type=checkbox]:not(#club-all)').forEach(cb => cb.checked = false);
      refreshClubTrigger();
      renderGroup();
    }}
  }});

  dropdown.querySelectorAll('input[type=checkbox]:not(#club-all)').forEach(cb => {{
    cb.addEventListener('change', () => {{
      if (cb.checked) {{ selectedClub.add(cb.value); allCb.checked = false; }}
      else {{ selectedClub.delete(cb.value); if (selectedClub.size === 0) allCb.checked = true; }}
      refreshClubTrigger();
      renderGroup();
    }});
  }});
}}

function getStats(p) {{
  return (p.stats && p.stats[String(activeRange)]) || null;
}}

function renderGroup() {{
  const players = filteredPlayers();
  const posLabel = selectedPos.size === 0 ? 'joueurs' : [...selectedPos].map(v=>POS_LABELS[v]).join(', ');
  const compLabel = selectedComp.size === 0 ? 'tous championnats confondus'
    : [...selectedComp].map(v => (DATA.players.find(p=>p.comp_slug===v)||{{}}).comp_name || v).join(', ');
  const sampleSentence = `Ce résultat prend en compte ${{players.length}} ${{posLabel}} (${{compLabel}}, ${{activeRange}} derniers matchs)`;
  document.getElementById('group-count').textContent = sampleSentence;

  // Aggregate
  const aggStats = {{}};
  for (const field of ALL_STATS) {{
    aggStats[field] = aggregate(players.map(p => {{ const s=getStats(p); return s?s[field]:null; }}));
  }}
  document.getElementById('agg-title').textContent = AGG_LABELS[aggMode];
  const aggEl = document.getElementById('agg-stats');
  aggEl.innerHTML = '';
  for (const field of ALL_STATS) {{
    if (aggStats[field] === null) continue;
    const cls = field==='score' ? scoreClass(aggStats[field]) : '';
    aggEl.insertAdjacentHTML('beforeend',
      `<div class="agg-stat"><div class="label">${{STAT_LABELS[field]||field}}</div><div class="value ${{cls}}">${{aggStats[field]}}</div></div>`
    );
  }}

  // Sort players
  const sorted = [...players].sort((a,b) => {{
    const sa = getStats(a), sb = getStats(b);
    const av = sa?sa[sortCol]:null, bv = sb?sb[sortCol]:null;
    if (av===null && bv===null) return 0;
    if (av===null) return 1; if (bv===null) return -1;
    return av<bv ? sortDir : av>bv ? -sortDir : 0;
  }});

  // Header
  const thead = document.getElementById('group-thead');
  thead.innerHTML = '<tr>' + [
    ['name','Joueur'], ['club','Club'], ['position','Poste'], ['comp_name','Championnat'],
    ...ALL_STATS.map(f => [f, STAT_LABELS[f]||f])
  ].map(([col,lbl]) => {{
    const isSorted = col === sortCol;
    return `<th data-col="${{col}}" class="${{isSorted?'sorted':''}}">${{lbl}} <span class="arr">${{isSorted?(sortDir===-1?'↓':'↑'):'↕'}}</span></th>`;
  }}).join('') + '</tr>';

  thead.querySelectorAll('th').forEach(th => {{
    th.addEventListener('click', () => {{
      if (sortCol === th.dataset.col) sortDir *= -1;
      else {{ sortCol = th.dataset.col; sortDir = -1; }}
      renderGroup();
    }});
  }});

  // Body
  const tbody = document.getElementById('group-tbody');
  if (!sorted.length) {{
    tbody.innerHTML = '';
    document.getElementById('group-no-data').style.display='';
    return;
  }}
  document.getElementById('group-no-data').style.display='none';
  tbody.innerHTML = sorted.map(p => {{
    const s = getStats(p);
    return `<tr>
      <td>${{p.name}}</td>
      <td>${{p.club}}</td>
      <td><span class="badge pos-${{p.position}}">${{POS_LABELS[p.position]||p.position}}</span></td>
      <td>${{p.comp_name}}</td>
      ${{ALL_STATS.map(f => {{
        const v = s?s[f]:null;
        const cls = f==='score' ? 'score-val '+scoreClass(v) : '';
        return `<td><span class="${{cls}}">${{fmt(v)}}</span></td>`;
      }}).join('')}}
    </tr>`;
  }}).join('');
}}

// ── Player view ───────────────────────────────────────────
function renderPlayer() {{
  const q = playerSearch.toLowerCase().trim();
  const results = document.getElementById('player-results');
  const countEl = document.getElementById('player-count');

  if (!q) {{
    countEl.textContent = '';
    results.innerHTML = '<div class="empty-state">Tapez un nom pour rechercher un joueur</div>';
    return;
  }}

  // Deduplicate by slug
  const seen = new Set();
  const matched = DATA.players.filter(p => {{
    if (!p.name.toLowerCase().includes(q)) return false;
    if (seen.has(p.slug)) return false;
    seen.add(p.slug);
    return true;
  }});

  countEl.textContent = matched.length + ' résultat' + (matched.length>1?'s':'');

  if (!matched.length) {{
    results.innerHTML = '<div class="empty-state">Aucun joueur trouvé</div>';
    return;
  }}

  results.innerHTML = matched.map(p => {{
    const comps = DATA.players.filter(x=>x.slug===p.slug).map(x=>x.comp_name);
    const uniqueComps = [...new Set(comps)].join(', ');
    return `<div class="player-card">
      <h2>${{p.name}}</h2>
      <div class="meta">
        <span class="badge pos-${{p.position}}">${{POS_LABELS[p.position]||p.position}}</span>
        &nbsp;${{p.club}} &nbsp;·&nbsp; ${{uniqueComps}}
      </div>
      <div class="range-tabs" data-slug="${{p.slug}}">
        ${{[5,10,40].map(n=>`<div class="range-tab ${{n===10?'active':''}}" data-n="${{n}}">${{n}} matchs</div>`).join('')}}
      </div>
      <div class="stats-grid" id="sg-${{p.slug}}">
        ${{renderStatsGrid(p.stats, 10)}}
      </div>
    </div>`;
  }}).join('');

  results.querySelectorAll('.range-tab').forEach(tab => {{
    tab.addEventListener('click', () => {{
      const card = tab.closest('.player-card');
      const slug = card.querySelector('.range-tabs').dataset.slug;
      const n = parseInt(tab.dataset.n);
      card.querySelectorAll('.range-tab').forEach(t=>t.classList.remove('active'));
      tab.classList.add('active');
      const player = DATA.players.find(p=>p.slug===slug);
      document.getElementById('sg-'+slug).innerHTML = renderStatsGrid(player.stats, n);
    }});
  }});
}}

function renderStatsGrid(stats, n) {{
  const s = stats && stats[String(n)];
  if (!s) return '<div style="color:#475569;font-size:.85rem">Pas de données pour cette range</div>';
  return ALL_STATS.map(f => {{
    if (s[f]===null||s[f]===undefined) return '';
    return `<div class="stat-card"><div class="slabel">${{STAT_LABELS[f]||f}}</div><div class="sval">${{s[f]}}</div></div>`;
  }}).join('');
}}

// ── Event listeners ───────────────────────────────────────
document.getElementById('range-select').addEventListener('change', e => {{
  activeRange = parseInt(e.target.value);
  renderGroup();
}});

// ── Multi-select helper ───────────────────────────────────
function setupMulti(wrapId, allCheckId, triggerId, state, labelFn) {{
  const wrap = document.getElementById(wrapId);
  const trigger = document.getElementById(triggerId);
  const allCb = document.getElementById(allCheckId);
  const itemCbs = [...wrap.querySelectorAll('.multi-dropdown input[type=checkbox]:not(#' + allCheckId + ')')];

  function updateTrigger() {{
    if (state.size === 0) {{
      trigger.textContent = 'Tous';
      trigger.classList.remove('has-selection');
    }} else {{
      const labels = [...state].map(labelFn);
      trigger.textContent = labels.length <= 2 ? labels.join(', ') : labels.length + ' sélectionnés';
      trigger.classList.add('has-selection');
    }}
  }}

  trigger.addEventListener('click', e => {{
    e.stopPropagation();
    wrap.classList.toggle('open');
  }});

  allCb.addEventListener('change', () => {{
    if (allCb.checked) {{
      state.clear();
      itemCbs.forEach(cb => cb.checked = false);
      updateTrigger();
      renderGroup();
    }}
  }});

  itemCbs.forEach(cb => {{
    cb.addEventListener('change', () => {{
      if (cb.checked) {{ state.add(cb.value); allCb.checked = false; }}
      else {{ state.delete(cb.value); if (state.size === 0) allCb.checked = true; }}
      updateTrigger();
      renderGroup();
    }});
  }});

  document.addEventListener('click', e => {{
    if (!wrap.contains(e.target)) wrap.classList.remove('open');
  }});
}}

setupMulti('pos-wrap', 'pos-all', 'pos-trigger', selectedPos,
  v => ({{Goalkeeper:'Gardien',Defender:'Défenseur',Midfielder:'Milieu',Forward:'Attaquant'}})[v] || v);

setupMulti('comp-wrap', 'comp-all', 'comp-trigger', selectedComp,
  v => (DATA.players.find(p=>p.comp_slug===v)||{{}}).comp_name || v);

// Group checkbox behaviour (shared)
function setupCompGroup(groupId) {{
  const groupCb = document.getElementById(groupId);
  if (!groupCb) return;
  const groupSlugs = groupCb.dataset.group.split(' ').filter(Boolean);
  const subCbs = groupSlugs.map(slug =>
    document.querySelector(`#comp-wrap input[value="${{slug}}"]`)
  ).filter(Boolean);

  function refreshTrigger() {{
    const trigger = document.getElementById('comp-trigger');
    if (selectedComp.size === 0) {{
      trigger.textContent = 'Tous'; trigger.classList.remove('has-selection');
    }} else {{
      const labels = [...selectedComp].map(v=>(DATA.players.find(p=>p.comp_slug===v)||{{}}).comp_name||v);
      trigger.textContent = labels.length <= 2 ? labels.join(', ') : labels.length + ' sélectionnés';
      trigger.classList.add('has-selection');
    }}
  }}

  groupCb.addEventListener('change', () => {{
    subCbs.forEach(cb => {{
      cb.checked = groupCb.checked;
      if (groupCb.checked) selectedComp.add(cb.value);
      else selectedComp.delete(cb.value);
    }});
    document.getElementById('comp-all').checked = selectedComp.size === 0;
    refreshTrigger();
    renderGroup();
  }});

  subCbs.forEach(cb => {{
    cb.addEventListener('change', () => {{
      groupCb.checked = subCbs.every(c => c.checked);
      groupCb.indeterminate = !groupCb.checked && subCbs.some(c => c.checked);
    }});
  }});
}}

setupCompGroup('comp-contender');
setupCompGroup('comp-challenger');

// Refresh club filter whenever competition changes
document.querySelectorAll('#comp-wrap input[type=checkbox]').forEach(cb => {{
  cb.addEventListener('change', () => updateClubFilter());
}});
updateClubFilter();

document.querySelectorAll('.agg-btn').forEach(btn => {{
  btn.addEventListener('click', () => {{
    document.querySelectorAll('.agg-btn').forEach(b=>b.classList.remove('active'));
    btn.classList.add('active');
    aggMode = btn.dataset.agg;
    renderGroup();
  }});
}});

document.getElementById('mins-slider').addEventListener('input', e => {{
  minMins = parseInt(e.target.value);
  document.getElementById('mins-val').textContent = '≥ ' + minMins + ' min';
  renderGroup();
}});

document.getElementById('player-search').addEventListener('input', e => {{
  playerSearch = e.target.value;
  renderPlayer();
}});

renderGroup();
</script>
</body>
</html>"""


if __name__ == "__main__":
    players, competitions = main()
    last_updated = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    html = generate_html(players, competitions, last_updated)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nDone — {len(players)} entries, {len(set(p['slug'] for p in players))} unique players → index.html", flush=True)
