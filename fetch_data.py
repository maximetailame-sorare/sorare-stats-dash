import json
import os
import sys
import time
import requests
from datetime import datetime
from collections import defaultdict

SORARE_API = "https://api.sorare.com/graphql"
POSITIONS = ["Goalkeeper", "Defender", "Midfielder", "Forward"]
RANGES = [5, 10, 20]
# With API key: complexity limit is 30 000 → batch 100 players (~200 each)
# Without API key: complexity limit is 500 → batch 1 player
BATCH_SIZE_WITH_KEY = 100
BATCH_SIZE_WITHOUT_KEY = 1
REQUEST_DELAY = 1.5

STAT_FIELDS = [
    "goals", "goalAssist", "yellowCard", "redCard", "minsPlayed",
    "cleanSheet", "saves", "totalPass", "accuratePass", "totalTackle",
    "interceptionWon", "wonContest", "duelWon", "lostCorners",
    "errorLeadToGoal", "ownGoals", "penaltyConceded", "penaltyKickMissed",
    "penaltySave",
]

STAT_LABELS = {
    "score": "Score SO5",
    "goals": "Buts",
    "goalAssist": "Passes D.",
    "yellowCard": "Carton J.",
    "redCard": "Carton R.",
    "minsPlayed": "Minutes",
    "cleanSheet": "Clean Sheet",
    "saves": "Arrêts",
    "totalPass": "Passes tot.",
    "accuratePass": "Passes réussies",
    "totalTackle": "Tacles",
    "interceptionWon": "Interceptions",
    "wonContest": "Duels gagnés",
    "duelWon": "Duels",
    "lostCorners": "Corners perdus",
    "errorLeadToGoal": "Erreurs menant au but",
    "ownGoals": "CSC",
    "penaltyConceded": "Penaltys concédés",
    "penaltyKickMissed": "Penaltys manqués",
    "penaltySave": "Penaltys arrêtés",
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
    "serie-a-it": "Serie A",
    "primera-liga-pt": "Primeira Liga",
    "spor-toto-super-lig": "Süper Lig",
    "premiership-gb-sct": "Scottish Premiership",
    "austrian-bundesliga": "Austrian Bundesliga",
    "superliga-argentina-de-futbol": "Superliga Argentina",
    "mlspa": "Major League Soccer",
    "j1-100-year-vision-league": "J1 League",
    "uefa-champions-league": "Champions League",
    "uefa-europa-league": "Europa League",
    "uefa-europa-conference-league": "Europa Conference League",
}



def build_headers():
    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get("SORARE_API_KEY")
    if api_key:
        headers["Authorization"] = f"APIKEY {api_key}"
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




def get_players_for_comp_position(comp_slug, position):
    query = f"""
    {{
      searchPlayers(
        advancedFilters: "sport:football AND active_competitions:{comp_slug} AND position:{position}",
        pageSize: 100
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
    if not data:
        return []
    hits = data.get("searchPlayers", {}).get("hits") or []
    return [
        {
            "slug": h["player"]["slug"],
            "name": h["player"].get("displayName") or h["player"]["slug"],
            "club": (h["player"].get("activeClub") or {}).get("name", ""),
        }
        for h in hits
        if h.get("player", {}).get("slug")
    ]


def fetch_stats_batch(slugs):
    """Fetch so5Scores for a batch of players using field aliases inside football{}."""
    stats_fields = "\n".join(STAT_FIELDS)
    aliases = "\n".join(
        f'p{i}: player(slug: "{slug}") {{ so5Scores(last: 20) {{ score playerGameStats {{ {stats_fields} }} }} }}'
        for i, slug in enumerate(slugs)
    )
    query = f"{{ football {{ {aliases} }} }}"
    data = gql(query)
    if not data:
        return {}
    football = data.get("football") or {}
    return {
        slugs[i]: (football.get(f"p{i}") or {}).get("so5Scores") or []
        for i in range(len(slugs))
    }


def compute_averages(so5_scores):
    """Returns {5: {...}, 10: {...}, 20: {...}} averages from game stats."""
    played = [
        g["playerGameStats"]
        for g in so5_scores
        if g.get("playerGameStats") and (g["playerGameStats"].get("minsPlayed") or 0) > 0
    ]
    sorare_scores = [g["score"] for g in so5_scores if g.get("score") is not None]

    result = {}
    for n in RANGES:
        games = played[:n]
        scores = sorare_scores[:n]
        if not games and not scores:
            result[n] = None
            continue
        stats = {}
        if scores:
            stats["score"] = round(sum(scores) / len(scores), 2)
        for field in STAT_FIELDS:
            values = [g[field] for g in games if g.get(field) is not None]
            stats[field] = round(sum(values) / len(values), 2) if values else None
        result[n] = stats
    return result


def main():
    competitions = COMPETITIONS
    print(f"Using {len(competitions)} competitions", flush=True)

    # Step 1: Collect all (player, comp, position) entries
    # slug -> { name, club, comps: [{comp_slug, comp_name, position}] }
    player_meta = {}
    # comp_position -> [slug, ...]
    comp_position_players = defaultdict(list)

    for comp_slug, comp_name in competitions.items():
        for position in POSITIONS:
            print(f"Listing {position}s — {comp_name}...", flush=True)
            players = get_players_for_comp_position(comp_slug, position)
            time.sleep(REQUEST_DELAY)
            for p in players:
                slug = p["slug"]
                if slug not in player_meta:
                    player_meta[slug] = {"name": p["name"], "club": p["club"], "comps": []}
                player_meta[slug]["comps"].append({
                    "comp_slug": comp_slug,
                    "comp_name": comp_name,
                    "position": position,
                })
                comp_position_players[(comp_slug, position)].append(slug)

    # Step 2: Fetch stats for all unique players
    all_slugs = list(player_meta.keys())
    total = len(all_slugs)
    batch_size = BATCH_SIZE_WITH_KEY if os.environ.get("SORARE_API_KEY") else BATCH_SIZE_WITHOUT_KEY
    print(f"\nFetching stats for {total} unique players (batch size: {batch_size})...", flush=True)
    player_stats = {}
    for i in range(0, total, batch_size):
        batch = all_slugs[i: i + batch_size]
        print(f"  {i}/{total}...", flush=True)
        results = fetch_stats_batch(batch)
        for slug, scores in results.items():
            player_stats[slug] = compute_averages(scores)
        time.sleep(REQUEST_DELAY)

    # Step 3: Build final player list (one entry per player×competition×position)
    all_players = []
    for slug, meta in player_meta.items():
        avgs = player_stats.get(slug, {})
        for comp_info in meta["comps"]:
            all_players.append({
                "slug": slug,
                "name": meta["name"],
                "club": meta["club"],
                "position": comp_info["position"],
                "comp_slug": comp_info["comp_slug"],
                "comp_name": comp_info["comp_name"],
                "stats": {str(n): avgs.get(n) for n in RANGES},
            })

    return all_players, competitions


def generate_html(players, competitions, last_updated):
    data_json = json.dumps({
        "last_updated": last_updated,
        "players": players,
        "stat_labels": STAT_LABELS,
    }, ensure_ascii=False)

    comp_buttons = "\n".join(
        f'<button class="filter-btn comp-btn" data-value="{slug}">{name}</button>'
        for slug, name in sorted(competitions.items(), key=lambda x: x[1])
    )

    all_stats = ["score"] + STAT_FIELDS
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
      <button class="filter-btn range-btn active" data-range="10">10 matchs</button>
      <button class="filter-btn range-btn" data-range="5">5 matchs</button>
      <button class="filter-btn range-btn" data-range="20">20 matchs</button>
    </div>
    <div class="filter-group">
      <span class="filter-label">Poste</span>
      <button class="filter-btn pos-btn active" data-value="all">Tous</button>
      <button class="filter-btn pos-btn" data-value="Goalkeeper">Gardien</button>
      <button class="filter-btn pos-btn" data-value="Defender">Défenseur</button>
      <button class="filter-btn pos-btn" data-value="Midfielder">Milieu</button>
      <button class="filter-btn pos-btn" data-value="Forward">Attaquant</button>
    </div>
    <div class="filter-group">
      <span class="filter-label">Championnat</span>
      <button class="filter-btn comp-btn active" data-value="all">Tous</button>
      {comp_buttons}
    </div>
  </div>

  <div id="group-count"></div>

  <div class="aggregate-box" id="agg-box">
    <h3 id="agg-title">Moyennes du groupe</h3>
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
const ALL_STATS = ["score", {', '.join(f'"{f}"' for f in STAT_FIELDS)}];
const POS_LABELS = {{Goalkeeper:'Gardien',Defender:'Défenseur',Midfielder:'Milieu',Forward:'Attaquant'}};

document.getElementById('last-updated').textContent = 'Mis à jour : ' + DATA.last_updated;

// ── State ──────────────────────────────────────────────────
let activeTab = 'group';
let activeRange = 10;
let activePos = 'all';
let activeComp = 'all';
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

// ── Group view ────────────────────────────────────────────
function filteredPlayers() {{
  return DATA.players.filter(p => {{
    if (activePos !== 'all' && p.position !== activePos) return false;
    if (activeComp !== 'all' && p.comp_slug !== activeComp) return false;
    return true;
  }});
}}

function getStats(p) {{
  return (p.stats && p.stats[String(activeRange)]) || null;
}}

function renderGroup() {{
  const players = filteredPlayers();
  const label = [
    activePos !== 'all' ? POS_LABELS[activePos] : 'Tous postes',
    activeComp !== 'all' ? (DATA.players.find(p=>p.comp_slug===activeComp)||{{}}).comp_name || activeComp : 'Tous championnats',
    activeRange + ' matchs'
  ].join(' · ');

  const posLabel = activePos !== 'all' ? POS_LABELS[activePos]+'s' : 'joueurs';
  const compLabel = activeComp !== 'all' ? (DATA.players.find(p=>p.comp_slug===activeComp)||{{}}).comp_name || activeComp : 'tous championnats confondus';
  const sampleSentence = `Ce résultat prend en compte ${{players.length}} ${{posLabel}} (${{compLabel}}, ${{activeRange}} derniers matchs)`;
  document.getElementById('group-count').textContent = sampleSentence;

  // Aggregate
  const aggStats = {{}};
  for (const field of ALL_STATS) {{
    aggStats[field] = avg(players.map(p => {{ const s=getStats(p); return s?s[field]:null; }}));
  }}
  document.getElementById('agg-title').textContent = 'Moyennes du groupe';
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
        ${{[5,10,20].map(n=>`<div class="range-tab ${{n===10?'active':''}}" data-n="${{n}}">${{n}} matchs</div>`).join('')}}
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
document.querySelectorAll('.range-btn').forEach(btn => {{
  btn.addEventListener('click', () => {{
    document.querySelectorAll('.range-btn').forEach(b=>b.classList.remove('active'));
    btn.classList.add('active');
    activeRange = parseInt(btn.dataset.range);
    renderGroup();
  }});
}});

document.querySelectorAll('.pos-btn').forEach(btn => {{
  btn.addEventListener('click', () => {{
    document.querySelectorAll('.pos-btn').forEach(b=>b.classList.remove('active'));
    btn.classList.add('active');
    activePos = btn.dataset.value;
    renderGroup();
  }});
}});

document.querySelectorAll('.comp-btn').forEach(btn => {{
  btn.addEventListener('click', () => {{
    document.querySelectorAll('.comp-btn').forEach(b=>b.classList.remove('active'));
    btn.classList.add('active');
    activeComp = btn.dataset.value;
    renderGroup();
  }});
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
