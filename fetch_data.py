import json
import os
import sys
import requests
from datetime import datetime

SORARE_API = "https://api.sorare.com/graphql"

POSITIONS = ["Goalkeeper", "Defender", "Midfielder", "Forward"]

# Slugs/keywords to exclude from dynamic competition discovery (cups, friendlies, etc.)
COMPETITION_EXCLUDE = {
    "friendl", "trophy", "shield", "copa-", "taca", "trofeo", "coupe-",
    "coppa-", "-pokal", "play-off", "super-cup", "supercopa", "open-cup",
    "hybrid", "asia-trophy", "tipsport", "emirates", "emperor-cup",
    "community", "torneos", "league-cup", "leagues-cup", "j-league-cup",
    "canadian-championship", "club-world-cup", "libertadores", "3-lig",
    "efl-trophy", "fa-cup", "concacaf",
}

# Used only if the dynamic discovery fails entirely
COMPETITIONS_FALLBACK = {
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
    "1-hnl": "SuperSport HNL",
    "superliga-argentina-de-futbol": "Superliga Argentina",
    "mlspa": "Major League Soccer",
    "liga-pro": "Liga Pro",
    "j1-100-year-vision-league": "J1 League",
    "uefa-champions-league": "Champions League",
    "uefa-europa-league": "Europa League",
    "uefa-europa-conference-league": "Europa Conference League",
}

DISCOVER_QUERY = """
{
  searchPlayers(advancedFilters: "sport:football", pageSize: 500) {
    hits {
      player {
        activeClub {
          activeCompetitions {
            slug
            displayName
          }
        }
      }
    }
  }
}
"""

QUERY = """
query SearchPlayers($filters: String!) {
  searchPlayers(
    advancedFilters: $filters,
    pageSize: 100,
  ) {
    nbHits
    hits {
      player {
        slug
        displayName
        position
        activeClub {
          name
        }
        averageStats(limit: LAST_10, type: WON_CONTEST) {
          score
          goals
          goalAssist
          cleanSheet
          yellowCard
          redCard
          minsPlayed
          saves
          ownGoals
          errorLeadToGoal
          penaltySaved
          penaltyMiss
        }
      }
    }
  }
}
"""


def build_headers() -> dict:
    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get("SORARE_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _is_league(slug: str) -> bool:
    return not any(kw in slug for kw in COMPETITION_EXCLUDE)


def get_competitions() -> dict:
    try:
        response = requests.post(
            SORARE_API,
            json={"query": DISCOVER_QUERY},
            headers=build_headers(),
            timeout=60,
        )
        response.raise_for_status()
        body = response.json()
        hits = body.get("data", {}).get("searchPlayers", {}).get("hits") or []

        found = {}
        for hit in hits:
            club = (hit.get("player") or {}).get("activeClub") or {}
            for c in club.get("activeCompetitions") or []:
                slug = c.get("slug", "")
                if slug and _is_league(slug):
                    found[slug] = c["displayName"]

        if found:
            print(f"Discovered {len(found)} competitions from API")
            return found
    except Exception as exc:
        print(f"Could not discover competitions ({exc}), using fallback list", file=sys.stderr)
    return COMPETITIONS_FALLBACK


def fetch_players(position: str, competition: str) -> list:
    filters = f"sport:football AND active_competitions:{competition} AND position:{position}"

    response = requests.post(
        SORARE_API,
        json={"query": QUERY, "variables": {"filters": filters}},
        headers=build_headers(),
        timeout=30,
    )
    response.raise_for_status()

    body = response.json()
    if "errors" in body:
        print(f"  GraphQL errors: {body['errors']}", file=sys.stderr)

    return body.get("data", {}).get("searchPlayers", {}).get("hits", [])


def round_stat(value):
    if value is None:
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def main():
    all_players = []
    competitions = get_competitions()

    for comp_slug, comp_name in competitions.items():
        for position in POSITIONS:
            print(f"Fetching {position}s — {comp_name}...")
            try:
                hits = fetch_players(position, comp_slug)
                for hit in hits:
                    player = hit.get("player") or {}
                    if not player.get("slug"):
                        continue

                    stats = player.get("averageStats") or {}
                    club = (player.get("activeClub") or {}).get("name", "")

                    all_players.append({
                        "slug": player["slug"],
                        "name": player.get("displayName") or player["slug"],
                        "position": position,
                        "competition_slug": comp_slug,
                        "competition_name": comp_name,
                        "club": club,
                        "score": round_stat(stats.get("score")),
                        "goals": round_stat(stats.get("goals")),
                        "assists": round_stat(stats.get("goalAssist")),
                        "clean_sheet": round_stat(stats.get("cleanSheet")),
                        "saves": round_stat(stats.get("saves")),
                        "yellow_cards": round_stat(stats.get("yellowCard")),
                        "red_cards": round_stat(stats.get("redCard")),
                        "mins_played": round_stat(stats.get("minsPlayed")),
                    })
            except Exception as exc:
                print(f"  Error: {exc}", file=sys.stderr)

    return all_players


def generate_html(players: list, last_updated: str) -> str:
    data_json = json.dumps({"last_updated": last_updated, "players": players}, ensure_ascii=False)

    competitions = {p["competition_slug"]: p["competition_name"] for p in players}
    comp_options = "\n".join(
        f'<button class="filter-btn comp-btn" data-value="{slug}">{name}</button>'
        for slug, name in sorted(competitions.items(), key=lambda x: x[1])
    )

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Sorare Stats Dashboard</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #0f172a; color: #e2e8f0; min-height: 100vh; }}
    header {{ background: #1e293b; border-bottom: 1px solid #334155; padding: 20px 32px; display: flex; align-items: center; justify-content: space-between; }}
    header h1 {{ font-size: 1.4rem; font-weight: 700; color: #f1f5f9; }}
    #last-updated {{ font-size: 0.78rem; color: #64748b; }}
    .controls {{ padding: 24px 32px 0; }}
    .filter-group {{ margin-bottom: 16px; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}
    .filter-label {{ font-size: 0.75rem; font-weight: 600; color: #94a3b8; text-transform: uppercase; letter-spacing: .05em; min-width: 100px; }}
    .filter-btn {{ background: #1e293b; border: 1px solid #334155; color: #94a3b8; padding: 6px 14px; border-radius: 6px; cursor: pointer; font-size: 0.82rem; transition: all .15s; }}
    .filter-btn:hover {{ border-color: #6366f1; color: #a5b4fc; }}
    .filter-btn.active {{ background: #6366f1; border-color: #6366f1; color: #fff; font-weight: 600; }}
    .search-wrap {{ padding: 16px 32px 0; }}
    #search {{ background: #1e293b; border: 1px solid #334155; color: #e2e8f0; padding: 8px 14px; border-radius: 6px; font-size: 0.9rem; width: 280px; }}
    #search::placeholder {{ color: #475569; }}
    #search:focus {{ outline: none; border-color: #6366f1; }}
    .table-wrap {{ padding: 24px 32px; overflow-x: auto; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.875rem; }}
    thead tr {{ background: #1e293b; }}
    th {{ padding: 10px 14px; text-align: left; color: #94a3b8; font-weight: 600; font-size: 0.75rem; text-transform: uppercase; letter-spacing: .05em; white-space: nowrap; cursor: pointer; user-select: none; }}
    th:hover {{ color: #c7d2fe; }}
    th .sort-arrow {{ margin-left: 4px; opacity: 0.4; }}
    th.sorted .sort-arrow {{ opacity: 1; color: #818cf8; }}
    td {{ padding: 10px 14px; border-bottom: 1px solid #1e293b; white-space: nowrap; }}
    tr:hover td {{ background: #1e293b88; }}
    .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.72rem; font-weight: 600; }}
    .pos-Goalkeeper {{ background: #164e63; color: #67e8f9; }}
    .pos-Defender {{ background: #14532d; color: #86efac; }}
    .pos-Midfielder {{ background: #312e81; color: #a5b4fc; }}
    .pos-Forward {{ background: #7f1d1d; color: #fca5a5; }}
    .score {{ font-weight: 700; color: #f8fafc; }}
    .score.high {{ color: #4ade80; }}
    .score.mid {{ color: #facc15; }}
    .score.low {{ color: #f87171; }}
    #count {{ padding: 0 32px 8px; font-size: 0.8rem; color: #64748b; }}
    .no-data {{ text-align: center; padding: 60px; color: #475569; }}
  </style>
</head>
<body>
  <header>
    <h1>Sorare Stats Dashboard</h1>
    <span id="last-updated"></span>
  </header>

  <div class="controls">
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
      {comp_options}
    </div>
  </div>

  <div class="search-wrap">
    <input id="search" type="text" placeholder="Rechercher un joueur ou un club…" />
  </div>

  <div id="count"></div>

  <div class="table-wrap">
    <table id="table">
      <thead>
        <tr>
          <th data-col="name">Joueur <span class="sort-arrow">↕</span></th>
          <th data-col="club">Club <span class="sort-arrow">↕</span></th>
          <th data-col="position">Poste <span class="sort-arrow">↕</span></th>
          <th data-col="competition_name">Championnat <span class="sort-arrow">↕</span></th>
          <th data-col="score">Score moy. <span class="sort-arrow">↕</span></th>
          <th data-col="goals">Buts <span class="sort-arrow">↕</span></th>
          <th data-col="assists">Passes D. <span class="sort-arrow">↕</span></th>
          <th data-col="clean_sheet">Clean Sheet <span class="sort-arrow">↕</span></th>
          <th data-col="saves">Arrêts <span class="sort-arrow">↕</span></th>
          <th data-col="yellow_cards">Jaunes <span class="sort-arrow">↕</span></th>
          <th data-col="red_cards">Rouges <span class="sort-arrow">↕</span></th>
          <th data-col="mins_played">Min. <span class="sort-arrow">↕</span></th>
        </tr>
      </thead>
      <tbody id="tbody"></tbody>
    </table>
    <div id="no-data" class="no-data" style="display:none">Aucun joueur trouvé</div>
  </div>

  <script>
    const RAW = {data_json};
    document.getElementById('last-updated').textContent = 'Mis à jour : ' + RAW.last_updated;

    let players = RAW.players;
    let activePos = 'all';
    let activeComp = 'all';
    let sortCol = 'score';
    let sortDir = -1;
    let searchVal = '';

    const POS_LABELS = {{ Goalkeeper: 'Gardien', Defender: 'Défenseur', Midfielder: 'Milieu', Forward: 'Attaquant' }};

    function scoreClass(s) {{
      if (s === null || s === undefined) return '';
      if (s >= 60) return 'high';
      if (s >= 40) return 'mid';
      return 'low';
    }}

    function fmt(v) {{
      return (v === null || v === undefined) ? '—' : v;
    }}

    function render() {{
      let data = players.filter(p => {{
        if (activePos !== 'all' && p.position !== activePos) return false;
        if (activeComp !== 'all' && p.competition_slug !== activeComp) return false;
        if (searchVal) {{
          const q = searchVal.toLowerCase();
          if (!p.name.toLowerCase().includes(q) && !p.club.toLowerCase().includes(q)) return false;
        }}
        return true;
      }});

      data.sort((a, b) => {{
        const av = a[sortCol] ?? (typeof a[sortCol] === 'number' ? -Infinity : '');
        const bv = b[sortCol] ?? (typeof b[sortCol] === 'number' ? -Infinity : '');
        if (av < bv) return sortDir;
        if (av > bv) return -sortDir;
        return 0;
      }});

      const tbody = document.getElementById('tbody');
      tbody.innerHTML = '';

      if (data.length === 0) {{
        document.getElementById('no-data').style.display = '';
        document.getElementById('count').textContent = '';
      }} else {{
        document.getElementById('no-data').style.display = 'none';
        document.getElementById('count').textContent = data.length + ' joueur' + (data.length > 1 ? 's' : '');
        data.forEach(p => {{
          const sc = scoreClass(p.score);
          tbody.insertAdjacentHTML('beforeend', `
            <tr>
              <td>${{p.name}}</td>
              <td>${{p.club}}</td>
              <td><span class="badge pos-${{p.position}}">${{POS_LABELS[p.position] || p.position}}</span></td>
              <td>${{p.competition_name}}</td>
              <td><span class="score ${{sc}}">${{fmt(p.score)}}</span></td>
              <td>${{fmt(p.goals)}}</td>
              <td>${{fmt(p.assists)}}</td>
              <td>${{fmt(p.clean_sheet)}}</td>
              <td>${{fmt(p.saves)}}</td>
              <td>${{fmt(p.yellow_cards)}}</td>
              <td>${{fmt(p.red_cards)}}</td>
              <td>${{fmt(p.mins_played)}}</td>
            </tr>`);
        }});
      }}

      document.querySelectorAll('th').forEach(th => {{
        th.classList.toggle('sorted', th.dataset.col === sortCol);
        if (th.dataset.col === sortCol) {{
          th.querySelector('.sort-arrow').textContent = sortDir === -1 ? '↓' : '↑';
        }} else {{
          th.querySelector('.sort-arrow').textContent = '↕';
        }}
      }});
    }}

    document.querySelectorAll('.pos-btn').forEach(btn => {{
      btn.addEventListener('click', () => {{
        document.querySelectorAll('.pos-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        activePos = btn.dataset.value;
        render();
      }});
    }});

    document.querySelectorAll('.comp-btn').forEach(btn => {{
      btn.addEventListener('click', () => {{
        document.querySelectorAll('.comp-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        activeComp = btn.dataset.value;
        render();
      }});
    }});

    document.getElementById('search').addEventListener('input', e => {{
      searchVal = e.target.value.trim();
      render();
    }});

    document.querySelectorAll('th[data-col]').forEach(th => {{
      th.addEventListener('click', () => {{
        if (sortCol === th.dataset.col) {{ sortDir *= -1; }}
        else {{ sortCol = th.dataset.col; sortDir = -1; }}
        render();
      }});
    }});

    render();
  </script>
</body>
</html>"""


if __name__ == "__main__":
    players = main()
    last_updated = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    html = generate_html(players, last_updated)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Done — {len(players)} players written to index.html")
