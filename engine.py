"""Liga dos Apostadores — motor v2.
Uso:
  python3 engine.py add-games games.json            # adiciona jogos (dia = data do kickoff em Dublin)
  python3 engine.py fill [ISO_AGORA]                # cria palpites que faltam, só p/ jogos que ainda não começaram
  python3 engine.py settle results.json             # apura
  python3 engine.py render                          # gera index.html
Banca: todo apostador começa com R$ 1.000. Stake padrão R$ 20 (2%); alguns usam outras gestões de banca.
Odds: probabilidades 1X2 do Sportradar (as mesmas dos mercados Kalshi/Polymarket) + Poisson para gols,
escanteios e cartões, com margem de casa de 6%. Sem odds de casa real por enquanto.
"""
import collections, json, math, sys, random, os, datetime, re
from functools import lru_cache

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state.json")
MARGIN = 0.94
BANCA = 1000.0
BASE = 20.0

LEAGUES = {
    "la_liga":  {"nome": "La Liga",    "gols": 2.55, "esc": 9.6, "cart": 4.9, "times": 20},
    "serie_a":  {"nome": "Serie A",    "gols": 2.60, "esc": 9.8, "cart": 4.4, "times": 20},
    "ligue_1":  {"nome": "Ligue 1",    "gols": 2.80, "esc": 9.3, "cart": 3.9, "times": 18},
    "bundesliga": {"nome": "Bundesliga", "gols": 3.10, "esc": 9.7, "cart": 4.0, "times": 18},
    "epl":      {"nome": "Premier League", "gols": 2.80, "esc": 10.3, "cart": 3.8, "times": 20},
    "mls":      {"nome": "MLS",        "gols": 3.00, "esc": 9.6, "cart": 4.0, "times": 30},
    "brasileirao": {"nome": "Brasileirão", "gols": 2.35, "esc": 9.8, "cart": 5.0, "times": 20},
    "primeira": {"nome": "Portugal",   "gols": 2.55, "esc": 9.6, "cart": 4.8, "times": 18},
    "eredivisie": {"nome": "Eredivisie", "gols": 3.10, "esc": 9.6, "cart": 3.6, "times": 18},
    "champions_league": {"nome": "Champions League", "gols": 2.95, "esc": 9.8, "cart": 4.0, "times": 36},
    "europa_league": {"nome": "Europa League", "gols": 2.75, "esc": 9.6, "cart": 4.2, "times": 36},
    "conference_league": {"nome": "Conference League", "gols": 2.8, "esc": 9.7, "cart": 4.2, "times": 36},
    "championship": {"nome": "Championship", "gols": 2.55, "esc": 10.0, "cart": 4.0, "times": 24},
    "serie_b": {"nome": "Serie B (Itália)", "gols": 2.4, "esc": 9.5, "cart": 4.6, "times": 20},
    "bundesliga_2": {"nome": "2. Bundesliga", "gols": 3.0, "esc": 9.6, "cart": 4.0, "times": 18},
    "la_liga_2": {"nome": "La Liga 2", "gols": 2.3, "esc": 9.4, "cart": 5.0, "times": 22},
    "j_league": {"nome": "J-League", "gols": 2.6, "esc": 9.8, "cart": 3.0, "times": 20},
    "liga_mx": {"nome": "Liga MX", "gols": 2.8, "esc": 9.6, "cart": 5.0, "times": 18},
    "colombia": {"nome": "Colômbia", "gols": 2.4, "esc": 9.5, "cart": 5.5, "times": 20},
    "escocia": {"nome": "Escócia", "gols": 2.7, "esc": 10.0, "cart": 3.5, "times": 12},
    "turquia": {"nome": "Turquia", "gols": 3.0, "esc": 9.8, "cart": 5.0, "times": 20},
    "belgica": {"nome": "Bélgica", "gols": 3.0, "esc": 9.7, "cart": 4.2, "times": 16},
    "argentina": {"nome": "Argentina", "gols": 2.1, "esc": 9.2, "cart": 5.5, "times": 28},
    "libertadores": {"nome": "Libertadores", "gols": 2.4, "esc": 9.5, "cart": 5.0, "times": 32},
    "sul_americana": {"nome": "Sul-Americana", "gols": 2.3, "esc": 9.5, "cart": 5.0, "times": 32},
    "austria": {"nome": "Áustria", "gols": 3.1, "esc": 9.7, "cart": 3.8, "times": 12},
    "dinamarca": {"nome": "Dinamarca", "gols": 2.9, "esc": 9.6, "cart": 3.8, "times": 12},
    "paraguai": {"nome": "Paraguai", "gols": 2.3, "esc": 9.5, "cart": 5.5, "times": 12},
    "equador": {"nome": "Equador", "gols": 2.5, "esc": 9.5, "cart": 5.5, "times": 16},
    "australia": {"nome": "Austrália", "gols": 3.0, "esc": 10.0, "cart": 3.5, "times": 12},
    "nba": {"nome": "NBA", "sport": "nba", "times": 30},
    "nhl": {"nome": "NHL", "sport": "nhl", "times": 32},
    "nfl": {"nome": "NFL", "sport": "nfl", "times": 32},
}
sport_of = lambda g: LEAGUES.get(g["league"], {}).get("sport", "futebol")

def pois(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)

def matrix(lh, la, n=10):
    return [[pois(i, lh) * pois(j, la) for j in range(n)] for i in range(n)]

def fit(w, base):
    best = None
    for a in range(4, 90):
        lh = a * 0.05
        for b in range(4, 90):
            la = b * 0.05
            m = matrix(lh, la, 9)
            ph = sum(m[i][j] for i in range(9) for j in range(9) if i > j)
            pd = sum(m[i][i] for i in range(9))
            err = (ph - w[0]) ** 2 + (pd - w[1]) ** 2 + (1 - ph - pd - w[2]) ** 2 + 0.004 * (lh + la - base) ** 2
            if best is None or err < best[0]:
                best = (err, lh, la)
    return round(best[1], 2), round(best[2], 2)

def model(g):
    L = LEAGUES[g["league"]]
    if L.get("sport"):   # esportes de dois resultados (sem empate): só vencedor
        wh, wa = g["w"]["home"] / 100, g["w"]["away"] / 100; t = wh + wa; wh, wa = wh / t, wa / t
        fav = "home" if wh >= wa else "away"
        return {"lh": 0, "la": 0, "w": (wh, 0, wa), "fav": fav, "dog": "away" if fav == "home" else "home",
                "pfav": max(wh, wa), "pdog": min(wh, wa), "gap": abs(wh - wa)}
    w = (g["w"]["home"] / 100, g["w"]["draw"] / 100, g["w"]["away"] / 100)
    lh, la = fit(w, L["gols"])
    gap = abs(w[0] - w[2])
    fav = "home" if w[0] >= w[2] else "away"
    return {"lh": lh, "la": la, "w": w, "fav": fav, "dog": "away" if fav == "home" else "home",
            "pfav": max(w[0], w[2]), "pdog": min(w[0], w[2]), "gap": gap,
            "esc": round(L["esc"] + 1.6 * gap, 2), "esc_share": round(0.5 + 0.32 * (w[0] - w[2]), 3),
            "cart": round(L["cart"] + 0.9 * (1 - gap) - 0.5, 2)}

# ---------- distribuições ----------
_DIST = {}
def dist(g, fam):
    key = (g["id"], fam)
    if key in _DIST: return _DIST[key]
    m = g["model"]
    if fam == "goals":
        H = 7; out = []
        a1, b1, a2, b2 = m["lh"] * .45, m["la"] * .45, m["lh"] * .55, m["la"] * .55
        for a in range(H):
            for b in range(H):
                pab = pois(a, a1) * pois(b, b1)
                for c in range(H):
                    for d in range(H):
                        out.append(({"hh": a, "ha": b, "fh": a + c, "fa": b + d}, pab * pois(c, a2) * pois(d, b2)))
    elif fam == "corners":
        ch, ca = m["esc"] * m["esc_share"], m["esc"] * (1 - m["esc_share"])
        out = [({"ch": i, "ca": j}, pois(i, ch) * pois(j, ca)) for i in range(25) for j in range(25)]
    else:
        out = [({"cards": k}, pois(k, m["cart"])) for k in range(20)]
    _DIST[key] = out
    return out

FAMILY = {"shots_ou": "shots", "sot_ou": "shots", "goal_before": "mins", "corners_ou": "corners", "corners_1x2": "corners", "corners_ah": "corners", "team_corners": "corners",
          "cards_ou": "cards"}

def sg(s, side):
    return (s["fh"], s["fa"]) if side == "home" else (s["fa"], s["fh"])

def ou_grade(total, line, over):
    if round(line * 4) % 4 in (1, 3):
        return (ou_grade(total, line - .25, over) + ou_grade(total, line + .25, over)) / 2
    d = total - line if over else line - total
    return 1 if d > 0 else (0 if d == 0 else -1)

def ah_grade(diff, line):
    if round(line * 4) % 4 in (1, 3):
        return (ah_grade(diff, line - .25) + ah_grade(diff, line + .25)) / 2
    d = diff + line
    return 1 if d > 0 else (0 if d == 0 else -1)

def r12(h, a):
    return "home" if h > a else ("away" if a > h else "draw")

def grade(spec, s):
    t = spec["t"]
    if t == "1x2": return 1 if r12(s["fh"], s["fa"]) == spec["sel"] else -1
    if t == "ml":
        r = r12(s["fh"], s["fa"]); return 0 if r == "draw" else (1 if r == spec["sel"] else -1)
    if t == "dc": return 1 if r12(s["fh"], s["fa"]) in (spec["sel"], "draw") else -1
    if t == "dnb":
        r = r12(s["fh"], s["fa"]); return 0 if r == "draw" else (1 if r == spec["sel"] else -1)
    if t == "ou": return ou_grade(s["fh"] + s["fa"], spec["line"], spec["over"])
    if t == "btts": return 1 if (s["fh"] > 0 and s["fa"] > 0) == spec["yes"] else -1
    if t == "ah":
        a, b = sg(s, spec["sel"]); return ah_grade(a - b, spec["line"])
    if t == "cs": return 1 if [s["fh"], s["fa"]] == list(spec["score"]) else -1
    if t == "team_ou":
        a, _ = sg(s, spec["sel"]); return ou_grade(a, spec["line"], spec["over"])
    if t == "ht_ou": return ou_grade(s["hh"] + s["ha"], spec["line"], spec["over"])
    if t == "ht_1x2": return 1 if r12(s["hh"], s["ha"]) == spec["sel"] else -1
    if t == "htft": return 1 if r12(s["hh"], s["ha"]) == spec["ht"] and r12(s["fh"], s["fa"]) == spec["ft"] else -1
    if t == "win_nil":
        a, b = sg(s, spec["sel"]); return 1 if a > b and b == 0 else -1
    if t in ("sh_more", "fh_more"):
        f = s["hh"] + s["ha"]; sec = s["fh"] + s["fa"] - f
        return 1 if (sec > f if t == "sh_more" else f > sec) else -1
    if t == "win_half":
        a, b = sg(s, spec["sel"]); h1a, h1b = (s["hh"], s["ha"]) if spec["sel"] == "home" else (s["ha"], s["hh"])
        w = (h1a > h1b) or (a - h1a > b - h1b); return 1 if w == spec.get("yes", True) else -1
    if t == "win_both":
        a, b = sg(s, spec["sel"]); h1a, h1b = (s["hh"], s["ha"]) if spec["sel"] == "home" else (s["ha"], s["hh"])
        return 1 if (h1a > h1b and a - h1a > b - h1b) == spec.get("yes", True) else -1
    if t == "sh_ou": return ou_grade(s["fh"] + s["fa"] - s["hh"] - s["ha"], spec["line"], spec["over"])
    if t == "res_btts": return 1 if r12(s["fh"], s["fa"]) == spec["sel"] and (s["fh"] > 0 and s["fa"] > 0) == spec["yes"] else -1
    if t == "oe": return 1 if ((s["fh"] + s["fa"]) % 2 == 1) == spec["odd"] else -1
    if t == "corners_ou": return ou_grade(s["ch"] + s["ca"], spec["line"], spec["over"])
    if t == "corners_1x2": return 1 if r12(s["ch"], s["ca"]) == spec["sel"] else -1
    if t == "corners_ah":
        d = s["ch"] - s["ca"] if spec["sel"] == "home" else s["ca"] - s["ch"]; return ah_grade(d, spec["line"])
    if t == "team_corners":
        c = s["ch"] if spec["sel"] == "home" else s["ca"]; return ou_grade(c, spec["line"], spec["over"])
    if t == "cards_ou": return ou_grade(s["cards"], spec["line"], spec["over"])
    if t in ("shots_ou", "sot_ou"):
        k = "shots_total" if t == "shots_ou" else "shots_on_target"
        tot = s["stats"]["home"][k] + s["stats"]["away"][k]
        return ou_grade(tot, spec["line"], spec["over"])
    if t == "goal_before":
        if s["fh"] + s["fa"] == 0: return -1 if spec["yes"] else 1
        cedo = s["g1"] <= spec["min"]
        return 1 if cedo == spec["yes"] else -1
    raise ValueError(t)

def real_odd(g, spec):
    """Odd real da casa (DraftKings via ESPN/Supabase) quando o mercado bate exatamente."""
    r = g.get("real")
    if not r: return None
    t = spec["t"]
    if t == "ml": return r.get("odd_casa" if spec["sel"] == "home" else "odd_fora")
    if t == "1x2": return r.get({"home": "odd_casa", "draw": "odd_empate", "away": "odd_fora"}[spec["sel"]])
    if t == "ou" and r.get("linha_gols") is not None and float(r["linha_gols"]) == spec["line"]:
        return r.get("odd_over" if spec["over"] else "odd_under")
    if t == "ah" and r.get("handicap_casa") is not None:
        hl = float(r["handicap_casa"])
        if spec["sel"] == "home" and spec["line"] == hl: return r.get("odd_hcp_casa")
        if spec["sel"] == "away" and spec["line"] == -hl: return r.get("odd_hcp_fora")
    if t == "dnb" or t == "dc":
        return None
    return None

# ---------- calibração com dados reais (calib.json gerado no Supabase a partir de ~9.400 jogos) ----------
CALIB = json.load(open(os.path.join(HERE, "calib.json"))) if os.path.exists(os.path.join(HERE, "calib.json")) else {}
PROP_MARGIN = 0.93   # casas cobram ~7% em mercados de escanteio, cartão e afins
HT_SEM_CALIB = {"ht_ou", "ht_1x2", "htft", "fh_more", "sh_more"}

def calib_prob(g, spec):
    """Probabilidade calibrada (frequência real por liga e força do favorito), ou None se o mercado não tem tabela."""
    if not CALIB: return None
    t = spec["t"]; f = g["model"]["fav"]; key = None; inv = False
    if t in HT_SEM_CALIB: return None   # tabela do 1º tempo saiu errada (25% de gol no 1º tempo; o real é ~75%): usa o modelo de Poisson
    if t == "btts": key, inv = "btts_yes", not spec["yes"]
    elif t == "ou" and spec["line"] in (0.5, 1.5, 2.5, 3.5, 4.5): key, inv = f"ou_{spec['line']}", not spec["over"]
    elif t == "oe": key, inv = "odd_goals", not spec["odd"]
    elif t == "ht_ou" and spec["line"] in (0.5, 1.5): key, inv = f"ht_ou_{spec['line']}", not spec["over"]
    elif t == "ht_1x2" and spec["sel"] in ("draw", f): key = "ht_draw" if spec["sel"] == "draw" else "ht_fav"
    elif t == "htft" and spec["ft"] == f and spec["ht"] in (f, "draw"): key = "htft_fav_fav" if spec["ht"] == f else "htft_draw_fav"
    elif t in ("sh_more", "fh_more"): key = t
    elif t == "team_ou":
        side = "fav" if spec["sel"] == f else "dog"
        k = f"{side}_ou_{spec['line']}"
        if k in CALIB.get("todas", {}): key, inv = k, not spec["over"]
    elif t == "win_nil" and spec["sel"] == f: key = "fav_win_nil"
    elif t == "corners_ou" and spec["line"] in (8.5, 9.5, 10.5, 11.5): key, inv = f"esc_{spec['line']}", not spec["over"]
    elif t == "corners_1x2" and spec["sel"] != "draw": key = "esc_fav_mais" if spec["sel"] == f else "esc_dog_mais"
    elif t == "corners_ah" and spec["sel"] == f and spec["line"] == -2.5: key = "esc_fav_m2.5"
    elif t == "team_corners" and spec.get("over"):
        if spec["sel"] == f and spec["line"] == 5.5: key = "esc_fav_5.5"
        if spec["sel"] != f and spec["line"] == 3.5: key = "esc_dog_3.5"
    elif t == "cards_ou" and spec["line"] in (3.5, 4.5, 5.5): key, inv = f"cart_{spec['line']}", not spec["over"]
    if not key: return None
    tab = CALIB.get(g["league"], CALIB.get("todas", {}))
    if key not in tab: tab = CALIB.get("todas", {})
    if key not in tab: return None
    if not any(tab[key]): tab = CALIB.get("todas", {})   # liga sem dados desse mercado (tabela zerada)
    if key not in tab or not any(tab[key]): return None
    pf = g["model"]["pfav"]
    b = 0 if pf < .40 else 1 if pf < .50 else 2 if pf < .60 else 3 if pf < .70 else 4 if pf < .80 else 5
    p = min(0.98, max(0.02, tab[key][b]))
    return 1 - p if inv else p

# chutes: frequência real 2021–2026 (hist_matches), por liga: média de chutes, no alvo e P(over) nas linhas
SHOTS = {"la_liga": (24.4, 8.4, {22.5: .608, 24.5: .48, 26.5: .341}, {7.5: .605, 8.5: .464, 9.5: .339}),
         "bundesliga": (26.3, 9.6, {22.5: .73, 24.5: .59, 26.5: .452}, {7.5: .716, 8.5: .598, 9.5: .484}),
         "primeira": (24.2, 8.4, {22.5: .598, 24.5: .467, 26.5: .336}, {7.5: .586, 8.5: .459, 9.5: .335}),
         "serie_a": (25.3, 8.4, {22.5: .672, 24.5: .532, 26.5: .388}, {7.5: .596, 8.5: .462, 9.5: .331}),
         "ligue_1": (24.8, 8.9, {22.5: .633, 24.5: .5, 26.5: .352}, {7.5: .645, 8.5: .526, 9.5: .404}),
         "epl": (25.9, 9.0, {22.5: .715, 24.5: .581, 26.5: .441}, {7.5: .662, 8.5: .533, 9.5: .406}),
         "eredivisie": (27.4, 9.8, {22.5: .793, 24.5: .668, 26.5: .541}, {7.5: .746, 8.5: .624, 9.5: .503})}
SHOTS["_"] = (25.2, 8.8, {22.5: .67, 24.5: .54, 26.5: .40}, {7.5: .63, 8.5: .51, 9.5: .39})

def special_prob(g, spec):
    t = spec["t"]
    if t in ("shots_ou", "sot_ou"):
        tab = SHOTS.get(g["league"], SHOTS["_"])[2 if t == "shots_ou" else 3]
        p = tab.get(spec["line"])
        if p is None: return None
        p *= 1 + 0.15 * (g["model"]["lh"] + g["model"]["la"] - LEAGUES[g["league"]]["gols"]) / LEAGUES[g["league"]]["gols"]  # jogo mais aberto, mais chutes
        p = min(.95, max(.05, p))
        return p if spec["over"] else 1 - p
    if t == "goal_before":
        lam = (g["model"]["lh"] + g["model"]["la"]) * spec["min"] / 90 * 0.92  # pouco menos gol no começo do jogo
        p = 1 - math.exp(-lam)
        return p if spec["yes"] else 1 - p
    return None

_PRICE = {}
def price(g, spec):
    key = (g["id"], json.dumps(spec, sort_keys=True), bool(g.get("real")))
    if key in _PRICE: return _PRICE[key]
    if spec["t"] == "ml":
        ro = real_odd(g, spec); p = g["model"]["w"][0 if spec["sel"] == "home" else 2]
        res = (round(p, 4), round(float(ro), 2) if ro else round(min(99, max(1.01, 1 + (1 / p - 1) * MARGIN)), 2)); _PRICE[key] = res; return res
    sp = special_prob(g, spec)
    if sp is not None:
        mg = PROP_MARGIN if spec["t"] != "goal_before" else MARGIN
        res = (round(sp, 4), round(min(99, max(1.01, 1 + (1 / sp - 1) * mg)), 2)); _PRICE[key] = res; return res
    ro = real_odd(g, spec)
    cp = None if ro else calib_prob(g, spec)
    if cp is not None:
        res = (round(cp, 4), round(min(99, max(1.01, 1 + (1 / cp - 1) * PROP_MARGIN)), 2))
        _PRICE[key] = res
        return res
    tot = pw = pl = 0
    for s, p in dist(g, FAMILY.get(spec["t"], "goals")):
        tot += p; gr = grade(spec, s)
        if gr > 0: pw += p * gr
        elif gr < 0: pl += p * -gr
    pw /= tot; pl /= tot
    prob = pw / (pw + pl) if pw + pl else .5
    fair = 1 + pl / pw if pw > 0 else 99
    res = (round(prob, 4), round(float(ro), 2) if ro else round(min(99, max(1.01, 1 + (fair - 1) * MARGIN)), 2))
    _PRICE[key] = res
    return res

def team(g, side): return g[side + "_name"]

def label(g, spec):
    t = spec["t"]; o = "Mais de" if spec.get("over") else "Menos de"
    n = lambda x: "Empate" if x == "draw" else team(g, x)
    sgn = lambda l: ("+" if l > 0 else "") + str(l)
    return {
        "ml": lambda: ("Vencedor", f"{n(spec.get('sel'))} vence"),
        "1x2": lambda: ("Resultado", "Empate" if spec.get("sel") == "draw" else f"{n(spec.get('sel'))} vence"),
        "dc": lambda: ("Dupla chance", f"{n(spec.get('sel'))} ou empate"),
        "dnb": lambda: ("Empate anula", n(spec.get("sel"))),
        "ou": lambda: ("Total de gols", f"{o} {spec.get('line')}"),
        "btts": lambda: ("Ambas marcam", "Sim" if spec.get("yes") else "Não"),
        "ah": lambda: ("Handicap asiático", f"{n(spec.get('sel'))} {sgn(spec.get('line', 0))}"),
        "cs": lambda: ("Placar exato", f"{spec['score'][0]} x {spec['score'][1]}" if "score" in spec else ""),
        "team_ou": lambda: ("Gols do time", f"{n(spec.get('sel'))}: {o} {spec.get('line')}"),
        "ht_ou": lambda: ("Gols no 1º tempo", f"{o} {spec.get('line')}"),
        "ht_1x2": lambda: ("Resultado 1º tempo", "Empate" if spec.get("sel") == "draw" else f"{n(spec.get('sel'))} vence"),
        "htft": lambda: ("Intervalo/Final", f"{n(spec.get('ht'))} / {n(spec.get('ft'))}"),
        "win_nil": lambda: ("Vence sem sofrer gol", n(spec.get("sel"))),
        "sh_more": lambda: ("Tempo com mais gols", "2º tempo"),
        "fh_more": lambda: ("Tempo com mais gols", "1º tempo"),
        "oe": lambda: ("Gols par/ímpar", "Ímpar" if spec.get("odd") else "Par"),
        "corners_ou": lambda: ("Escanteios", f"{o} {spec.get('line')}"),
        "corners_1x2": lambda: ("Mais escanteios", n(spec.get("sel"))),
        "corners_ah": lambda: ("Handicap de escanteios", f"{n(spec.get('sel'))} {sgn(spec.get('line', 0))}"),
        "team_corners": lambda: ("Escanteios do time", f"{n(spec.get('sel'))}: {o} {spec.get('line')}"),
        "cards_ou": lambda: ("Cartões", f"{o} {spec.get('line')}"),
        "shots_ou": lambda: ("Chutes no jogo", f"{o} {spec.get('line')}"),
        "sot_ou": lambda: ("Chutes no alvo", f"{o} {spec.get('line')}"),
        "win_half": lambda: ("Vencer um dos tempos", f"{n(spec.get('sel'))} vence pelo menos um tempo"),
        "win_both": lambda: ("Vencer os dois tempos", f"{n(spec.get('sel'))} vence os dois tempos"),
        "sh_ou": lambda: ("Gols no 2º tempo", f"{o} {spec.get('line')}"),
        "res_btts": lambda: ("Resultado e ambas marcam", f"{n(spec.get('sel'))} e ambas {'marcam' if spec.get('yes') else 'não marcam'}"),
        "goal_before": lambda: ("Minuto do 1º gol", f"Gol até os {spec.get('min')} min" if spec.get('yes') else f"Sem gol até os {spec.get('min')} min"),
    }[t]()

# ---------- helpers de estratégia ----------
fav = lambda g: g["model"]["fav"]
dog = lambda g: g["model"]["dog"]
pf = lambda g: g["model"]["pfav"]
pd_ = lambda g: g["model"]["pdog"]
gap = lambda g: g["model"]["gap"]
xg = lambda g: g["model"]["lh"] + g["model"]["la"]
lg = lambda g: g["league"]
nteams = lambda g: LEAGUES[g["league"]]["times"]
POPULARES = {"Real Madrid", "Paris Saint-Germain", "AS Roma", "Inter Miami CF", "FC Barcelona", "Manchester United",
             "Liverpool FC", "Juventus Turin", "Bayern Munich", "AC Milan", "Inter Milano", "Arsenal FC", "Chelsea FC",
             "Barcelona", "Liverpool", "Juventus", "Milan", "Inter", "Chelsea", "Napoli", "SSC Napoli", "Manchester City"}

def S(t, **kw):
    d = {"t": t}; d.update(kw); return d

def cs_rank(g, k):
    m = matrix(g["model"]["lh"], g["model"]["la"], 8)
    cells = sorted(((m[i][j], i, j) for i in range(8) for j in range(8)), reverse=True)
    return [cells[k][1], cells[k][2]]

def candidates(g):
    f, d = fav(g), dog(g)
    c = [S("1x2", sel="home"), S("1x2", sel="draw"), S("1x2", sel="away"), S("dc", sel=f), S("dc", sel=d),
         S("dnb", sel=f), S("btts", yes=True), S("btts", yes=False), S("ah", sel=f, line=-1.0), S("ah", sel=f, line=-1.5),
         S("ah", sel=d, line=1.0), S("ah", sel=d, line=1.5), S("team_ou", sel=f, line=1.5, over=True),
         S("team_ou", sel=d, line=0.5, over=True), S("ht_ou", line=0.5, over=True), S("corners_ou", line=9.5, over=True),
         S("corners_ou", line=9.5, over=False), S("cards_ou", line=4.5, over=True), S("cards_ou", line=4.5, over=False),
         S("htft", ht=f, ft=f), S("cs", score=cs_rank(g, 0))]
    for line in (1.5, 2.5, 3.5):
        c += [S("ou", line=line, over=True), S("ou", line=line, over=False)]
    return c

def odds_pick(g, lo, hi, minp=0.0, target=None):
    best = None
    for s in candidates(g):
        p, o = price(g, s)
        if lo <= o <= hi and p >= minp:
            score = -abs(o - target) if target else p
            if best is None or score > best[0]: best = (score, s)
    return best[1] if best else None

def rnd_pick(g, r, wide=False):
    pool = [S("1x2", sel=r.choice(["home", "draw", "away"])), S("ou", line=2.5, over=r.random() < .5),
            S("btts", yes=r.random() < .5), S("corners_ou", line=9.5, over=r.random() < .5)]
    if wide:
        pool += [S("ah", sel=r.choice(["home", "away"]), line=r.choice([-1.0, -0.5, 0.5, 1.0])),
                 S("cards_ou", line=4.5, over=r.random() < .5), S("ht_ou", line=0.5, over=r.random() < .5)]
    return r.choice(pool)

def ppg_better(g): return "home" if g["home_ppg"] > g["away_ppg"] else "away"
def rank_better(g): return "home" if g["home_rank"] < g["away_rank"] else "away"
def rdiff(g): return abs(g["home_rank"] - g["away_rank"])
def mid_table(g):
    n = nteams(g); return all(n / 3 < g[k] <= 2 * n / 3 for k in ("home_rank", "away_rank"))

# ---------- apostadores ----------
B = []
def bet(id, nome, origem, estrategia, mercado, regra, rule, stake="flat", sport="futebol"):
    B.append({"id": id, "nome": nome, "origem": origem, "estrategia": estrategia, "mercado": mercado,
              "regra": regra, "rule": rule, "stake": stake, "sport": sport})

R = "Resultado (1X2)"
bet("goku", "Goku", "Dragon Ball", "Favorito puro", R, "Aposta sempre no time com maior chance de vencer.", lambda g, r: S("1x2", sel=fav(g)))
bet("vegeta", "Vegeta", "Dragon Ball", "Só favorito forte", R, "Favorito só com 65% ou mais de chance.", lambda g, r: S("1x2", sel=fav(g)) if pf(g) >= .65 else None)
bet("gohan", "Gohan", "Dragon Ball", "Favorito moderado", R, "Favorito só quando a chance fica entre 50% e 65%, onde a odd paga melhor.", lambda g, r: S("1x2", sel=fav(g)) if .50 <= pf(g) < .65 else None)
bet("piccolo", "Piccolo", "Dragon Ball", "Favorito claro", R, "Favorito com 55% ou mais.", lambda g, r: S("1x2", sel=fav(g)) if pf(g) >= .55 else None)
bet("broly", "Broly", "Dragon Ball", "Super favorito", R, "Só o favoritaço, com 80% ou mais.", lambda g, r: S("1x2", sel=fav(g)) if pf(g) >= .80 else None)
bet("naruto", "Naruto", "Naruto", "Caçador de zebras", R, "Vitória do azarão quando ele tem entre 15% e 40%.", lambda g, r: S("1x2", sel=dog(g)) if .15 <= pd_(g) <= .40 else None)
bet("sasuke", "Sasuke", "Naruto", "Zebra média", R, "Azarão com 25% a 40%: zebra que acontece com frequência.", lambda g, r: S("1x2", sel=dog(g)) if .25 <= pd_(g) <= .40 else None)
bet("rocklee", "Rock Lee", "Naruto", "Zebra longa", R, "Azarão com menos de 20%: acerta pouco, paga muito.", lambda g, r: S("1x2", sel=dog(g)) if pd_(g) < .20 else None)
bet("shikamaru", "Shikamaru", "Naruto", "Empate tático", R, "Empate em jogos equilibrados (diferença menor que 15 pontos).", lambda g, r: S("1x2", sel="draw") if gap(g) < .15 else None)
bet("hinata", "Hinata", "Naruto", "Empate puro", R, "Empate só em jogo muito parelho (diferença menor que 10 pontos).", lambda g, r: S("1x2", sel="draw") if gap(g) < .10 else None)
bet("tsunade", "Tsunade", "Naruto", "Empate de meio de tabela", R, "Empate quando os dois times estão no terço do meio da tabela e o jogo é equilibrado.", lambda g, r: S("1x2", sel="draw") if mid_table(g) and gap(g) < .25 else None)
bet("rocky", "Rocky Balboa", "Rocky", "Mandante sempre", R, "Vitória do mandante em todos os jogos.", lambda g, r: S("1x2", sel="home"))
bet("apollo", "Apollo Creed", "Rocky", "Mandante favorito", R, "Mandante só quando ele é o favorito.", lambda g, r: S("1x2", sel="home") if fav(g) == "home" else None)
bet("indiana", "Indiana Jones", "Indiana Jones", "Visitante sempre", R, "Vitória do visitante em todos os jogos.", lambda g, r: S("1x2", sel="away"))
bet("hansolo", "Han Solo", "Star Wars", "Visitante favorito", R, "Visitante só quando ele é o favorito.", lambda g, r: S("1x2", sel="away") if fav(g) == "away" else None)
bet("sherlock", "Sherlock Holmes", "Sherlock", "Pontos por jogo", R, "Quem pontua mais por jogo, se a diferença passar de 0,4.", lambda g, r: S("1x2", sel=ppg_better(g)) if abs(g["home_ppg"] - g["away_ppg"]) > .4 else None)
bet("watson", "Dr. Watson", "Sherlock", "Pontos por jogo, diferença grande", R, "Quem pontua mais, só com diferença acima de 0,8 por jogo.", lambda g, r: S("1x2", sel=ppg_better(g)) if abs(g["home_ppg"] - g["away_ppg"]) > .8 else None)
bet("poirot", "Hercule Poirot", "Agatha Christie", "Tabela 5+ posições", R, "Aposta no melhor colocado quando a distância é de 5 posições ou mais.", lambda g, r: S("1x2", sel=rank_better(g)) if rdiff(g) >= 5 else None)
bet("gus", "Gus Fring", "Breaking Bad", "Tabela 12+ posições", R, "Melhor colocado só com 12 posições ou mais de vantagem.", lambda g, r: S("1x2", sel=rank_better(g)) if rdiff(g) >= 12 else None)
bet("walter", "Walter White", "Breaking Bad", "Mandante bem acima na tabela", R, "Mandante 8 ou mais posições acima do visitante.", lambda g, r: S("1x2", sel="home") if g["away_rank"] - g["home_rank"] >= 8 else None)
bet("jesse", "Jesse Pinkman", "Breaking Bad", "Tabela contra o mercado", R, "Quando o time que pontua mais é o azarão nas probabilidades, aposta nele.", lambda g, r: S("1x2", sel=ppg_better(g)) if ppg_better(g) != fav(g) else None)

D = "Dupla chance"
bet("lisa", "Lisa Simpson", "Os Simpsons", "Dupla chance do favorito", D, "Favorito vence ou empata.", lambda g, r: S("dc", sel=fav(g)))
bet("bart", "Bart Simpson", "Os Simpsons", "Dupla chance do azarão", D, "Azarão vence ou empata.", lambda g, r: S("dc", sel=dog(g)))
bet("marge", "Marge Simpson", "Os Simpsons", "Azarão em jogo parelho", D, "Azarão vence ou empata, só em jogo equilibrado.", lambda g, r: S("dc", sel=dog(g)) if gap(g) < .20 else None)
bet("maggie", "Maggie Simpson", "Os Simpsons", "Mandante não perde", D, "Mandante vence ou empata, em todos os jogos.", lambda g, r: S("dc", sel="home"))
bet("burro", "Burro", "Shrek", "Contra favorito visitante", D, "Quando o visitante é favorito, aposta que o mandante não perde.", lambda g, r: S("dc", sel="home") if fav(g) == "away" else None)

E = "Empate anula"
bet("yoda", "Yoda", "Star Wars", "Empate anula do favorito", E, "Favorito vence; empate devolve.", lambda g, r: S("dnb", sel=fav(g)))
bet("simba", "Simba", "O Rei Leão", "Rei em casa", E, "Empate anula no mandante, se ele estiver na metade de cima da tabela.", lambda g, r: S("dnb", sel="home") if g["home_rank"] <= nteams(g) / 2 else None)
bet("obiwan", "Obi-Wan Kenobi", "Star Wars", "Visitante protegido", E, "Empate anula no visitante, em todos os jogos.", lambda g, r: S("dnb", sel="away"))

G = "Total de gols"
bet("luffy", "Luffy", "One Piece", "Over 2.5 sempre", G, "Mais de 2,5 gols em todos os jogos.", lambda g, r: S("ou", line=2.5, over=True))
bet("zoro", "Zoro", "One Piece", "Under 2.5 sempre", G, "Menos de 2,5 gols em todos os jogos.", lambda g, r: S("ou", line=2.5, over=False))
bet("tanjiro", "Tanjiro", "Demon Slayer", "Over 1.5 seguro", G, "Mais de 1,5 gol.", lambda g, r: S("ou", line=1.5, over=True))
bet("giyu", "Giyu", "Demon Slayer", "Under 1.5 sempre", G, "Jogo travado: no máximo 1 gol.", lambda g, r: S("ou", line=1.5, over=False))
bet("zenitsu", "Zenitsu", "Demon Slayer", "Over 3.5 explosivo", G, "Mais de 3,5 gols quando o modelo espera 2,9 ou mais.", lambda g, r: S("ou", line=3.5, over=True) if xg(g) >= 2.9 else None)
bet("inosuke", "Inosuke", "Demon Slayer", "Over 4.5 sem medo", G, "Mais de 4,5 gols em todos os jogos.", lambda g, r: S("ou", line=4.5, over=True))
bet("nezuko", "Nezuko", "Demon Slayer", "Under 3.5 conservador", G, "Menos de 3,5 gols.", lambda g, r: S("ou", line=3.5, over=False))
bet("muzan", "Muzan", "Demon Slayer", "Under 4.5 sempre", G, "Menos de 4,5 gols: acerta muito, paga pouco.", lambda g, r: S("ou", line=4.5, over=False))
bet("nami", "Nami", "One Piece", "Linha 2.5 pelo modelo", G, "Over ou under 2,5 conforme os gols esperados.", lambda g, r: S("ou", line=2.5, over=xg(g) > 2.6))
bet("robin", "Nico Robin", "One Piece", "Linha 1.5 pelo modelo", G, "Over ou under 1,5, o mais provável no modelo.", lambda g, r: S("ou", line=1.5, over=price(g, S("ou", line=1.5, over=True))[0] >= .5))
bet("franky", "Franky", "One Piece", "Linha 3.5 pelo modelo", G, "Over 3,5 se o modelo esperar 3,3 gols ou mais; senão under 3,5.", lambda g, r: S("ou", line=3.5, over=xg(g) >= 3.3))
bet("bulma", "Bulma", "Dragon Ball", "Over asiático 2.25", "Gols asiático", "Over 2,25: metade em 2 e metade em 2,5.", lambda g, r: S("ou", line=2.25, over=True))
bet("brook", "Brook", "One Piece", "Over asiático 2.75", "Gols asiático", "Over 2,75: metade em 2,5 e metade em 3.", lambda g, r: S("ou", line=2.75, over=True))
bet("chopper", "Chopper", "One Piece", "Under asiático 2.75", "Gols asiático", "Under 2,75: metade em 2,5 e metade em 3.", lambda g, r: S("ou", line=2.75, over=False))
bet("hulk", "Hulk", "Marvel", "Over em jogo desequilibrado", G, "Mais de 2,5 gols só com favorito de 70% ou mais.", lambda g, r: S("ou", line=2.5, over=True) if pf(g) >= .70 else None)
bet("elsa", "Elsa", "Frozen", "Jogo frio", G, "Menos de 2,5 gols só em jogo equilibrado.", lambda g, r: S("ou", line=2.5, over=False) if gap(g) < .20 else None)

L_ = "Especialista de liga"
bet("thor", "Thor", "Marvel", "Over 2.5 na MLS", L_, "Só MLS: mais de 2,5 gols.", lambda g, r: S("ou", line=2.5, over=True) if lg(g) == "mls" else None)
bet("loki", "Loki", "Marvel", "Under 2.5 na Serie A", L_, "Só Serie A: menos de 2,5 gols.", lambda g, r: S("ou", line=2.5, over=False) if lg(g) == "serie_a" else None)
bet("capitao", "Capitão América", "Marvel", "Over 2.5 na Bundesliga", L_, "Só Bundesliga: mais de 2,5 gols.", lambda g, r: S("ou", line=2.5, over=True) if lg(g) == "bundesliga" else None)
bet("viuva", "Viúva Negra", "Marvel", "Under 2.5 na La Liga", L_, "Só La Liga: menos de 2,5 gols.", lambda g, r: S("ou", line=2.5, over=False) if lg(g) == "la_liga" else None)
bet("frodo", "Frodo", "O Senhor dos Anéis", "Favorito na MLS", L_, "Só MLS: vitória do favorito.", lambda g, r: S("1x2", sel=fav(g)) if lg(g) == "mls" else None)
bet("sam", "Sam", "O Senhor dos Anéis", "Mandante na Ligue 1", L_, "Só Ligue 1: vitória do mandante.", lambda g, r: S("1x2", sel="home") if lg(g) == "ligue_1" else None)
bet("gandalf", "Gandalf", "O Senhor dos Anéis", "Over 2.5 na Premier", L_, "Só Premier League: mais de 2,5 gols.", lambda g, r: S("ou", line=2.5, over=True) if lg(g) == "epl" else None)
bet("aragorn", "Aragorn", "O Senhor dos Anéis", "Ambas marcam na Bundesliga", L_, "Só Bundesliga: ambos marcam.", lambda g, r: S("btts", yes=True) if lg(g) == "bundesliga" else None)
bet("gimli", "Gimli", "O Senhor dos Anéis", "Empate na Serie A", L_, "Só Serie A, em jogo equilibrado: empate.", lambda g, r: S("1x2", sel="draw") if lg(g) == "serie_a" and gap(g) < .20 else None)

A = "Ambas marcam"
bet("sanji", "Sanji", "One Piece", "Ambas marcam sempre", A, "Ambos os times marcam.", lambda g, r: S("btts", yes=True))
bet("usopp", "Usopp", "One Piece", "Ambas não marcam", A, "Pelo menos um time passa em branco.", lambda g, r: S("btts", yes=False))
bet("lawliet", "L", "Death Note", "Ambas marcam pelo modelo", A, "Sim ou Não, o mais provável no modelo.", lambda g, r: S("btts", yes=price(g, S("btts", yes=True))[0] >= .5))
bet("misa", "Misa Amane", "Death Note", "Ambas marcam em jogo parelho", A, "Sim, só em jogo equilibrado.", lambda g, r: S("btts", yes=True) if gap(g) < .20 else None)
bet("ryuk", "Ryuk", "Death Note", "Favorito forte não sofre", A, "Não, quando o favorito tem 70% ou mais.", lambda g, r: S("btts", yes=False) if pf(g) >= .70 else None)

C = "Placar exato"
bet("light", "Light Yagami", "Death Note", "Placar mais provável", C, "O placar com maior probabilidade no modelo.", lambda g, r: S("cs", score=cs_rank(g, 0)))
bet("near", "Near", "Death Note", "Segundo placar mais provável", C, "O segundo placar mais provável: odd maior.", lambda g, r: S("cs", score=cs_rank(g, 1)))
bet("mello", "Mello", "Death Note", "1 x 1 sempre", C, "Crava 1 x 1 em todos os jogos.", lambda g, r: S("cs", score=[1, 1]))
bet("rem", "Rem", "Re:Zero", "2 x 1 do favorito", C, "Favorito vence por 2 x 1.", lambda g, r: S("cs", score=[2, 1] if fav(g) == "home" else [1, 2]))

H = "Handicap asiático"
bet("itachi", "Itachi", "Naruto", "Favorito -1.5", H, "Favorito vence por 2+, só com 70% ou mais.", lambda g, r: S("ah", sel=fav(g), line=-1.5) if pf(g) >= .70 else None)
bet("genos", "Genos", "One Punch Man", "Favorito -1.0", H, "Favorito -1: ganha vencendo por 2+, devolve vencendo por 1.", lambda g, r: S("ah", sel=fav(g), line=-1.0) if pf(g) >= .55 else None)
bet("tony", "Tony Stark", "Marvel", "Favorito -0.75", H, "Favorito -0.75 quando ele tem entre 50% e 75%.", lambda g, r: S("ah", sel=fav(g), line=-0.75) if .50 <= pf(g) <= .75 else None)
bet("bang", "Bang", "One Punch Man", "Favorito -0.25", H, "Favorito -0.25: empate perde só metade.", lambda g, r: S("ah", sel=fav(g), line=-0.25))
bet("saitama", "Saitama", "One Punch Man", "Goleada -2.5", H, "Favorito vence por 3+, só com 80% ou mais.", lambda g, r: S("ah", sel=fav(g), line=-2.5) if pf(g) >= .80 else None)
bet("sonic", "Sonic", "Sonic", "Azarão +0.25", H, "Azarão +0.25: empate ganha metade.", lambda g, r: S("ah", sel=dog(g), line=0.25))
bet("garou", "Garou", "One Punch Man", "Azarão +0.5", H, "Azarão não perde.", lambda g, r: S("ah", sel=dog(g), line=0.5))
bet("kakashi", "Kakashi", "Naruto", "Azarão +1", H, "Azarão +1: ganha se não perder, devolve se perder por 1.", lambda g, r: S("ah", sel=dog(g), line=1.0))
bet("king", "King", "One Punch Man", "Azarão +1.5", H, "Azarão perde por no máximo 1 gol.", lambda g, r: S("ah", sel=dog(g), line=1.5))
bet("fubuki", "Fubuki", "One Punch Man", "Azarão +2", H, "Azarão +2: devolve se perder por 2.", lambda g, r: S("ah", sel=dog(g), line=2.0) if pf(g) >= .60 else None)
bet("shrek", "Shrek", "Shrek", "Contra os times de massa", H, "Contra o clube popular, +1.5 no adversário.", lambda g, r: S("ah", sel="away", line=1.5) if g["home_name"] in POPULARES else (S("ah", sel="home", line=1.5) if g["away_name"] in POPULARES else None))

T = "Gols do time"
bet("mario", "Mario", "Super Mario", "Favorito 2+ gols", T, "Favorito marca mais de 1,5 gol.", lambda g, r: S("team_ou", sel=fav(g), line=1.5, over=True))
bet("luigi", "Luigi", "Super Mario", "Favorito marca", T, "Favorito marca pelo menos 1.", lambda g, r: S("team_ou", sel=fav(g), line=0.5, over=True))
bet("peach", "Peach", "Super Mario", "Favorito 3+ gols", T, "Favorito faz 3 ou mais.", lambda g, r: S("team_ou", sel=fav(g), line=2.5, over=True) if pf(g) >= .60 else None)
bet("pikachu", "Pikachu", "Pokémon", "Azarão marca", T, "Azarão faz pelo menos 1.", lambda g, r: S("team_ou", sel=dog(g), line=0.5, over=True))
bet("bowser", "Bowser", "Super Mario", "Azarão em branco", T, "Azarão não marca.", lambda g, r: S("team_ou", sel=dog(g), line=0.5, over=False))
bet("yoshi", "Yoshi", "Super Mario", "Azarão no máximo 1", T, "Azarão faz no máximo 1 gol.", lambda g, r: S("team_ou", sel=dog(g), line=1.5, over=False))
bet("toad", "Toad", "Super Mario", "Mandante 2+ gols", T, "Mandante marca mais de 1,5 gol.", lambda g, r: S("team_ou", sel="home", line=1.5, over=True))
bet("wario", "Wario", "Super Mario", "Visitante marca", T, "Visitante faz pelo menos 1.", lambda g, r: S("team_ou", sel="away", line=0.5, over=True))
bet("daisy", "Daisy", "Super Mario", "Visitante 2+ gols", T, "Visitante marca mais de 1,5.", lambda g, r: S("team_ou", sel="away", line=1.5, over=True))
bet("batman", "Batman", "Batman", "Vitória sem sofrer gol", "Vence sem sofrer gol", "Favorito vence sem sofrer gol, com 60% ou mais.", lambda g, r: S("win_nil", sel=fav(g)) if pf(g) >= .60 else None)
bet("alfred", "Alfred", "Batman", "Mandante sem sofrer gol", T, "Visitante não marca.", lambda g, r: S("team_ou", sel="away", line=0.5, over=False))

P = "Primeiro tempo"
bet("neo", "Neo", "Matrix", "Gol no 1º tempo", P, "Pelo menos um gol antes do intervalo.", lambda g, r: S("ht_ou", line=0.5, over=True))
bet("trinity", "Trinity", "Matrix", "1º tempo 0 x 0", P, "Nenhum gol no primeiro tempo.", lambda g, r: S("ht_ou", line=0.5, over=False))
bet("morpheus", "Morpheus", "Matrix", "1º tempo travado", P, "Menos de 1,5 gol no primeiro tempo.", lambda g, r: S("ht_ou", line=1.5, over=False))
bet("haku", "Haku", "A Viagem de Chihiro", "1º tempo agitado", P, "Mais de 1,5 gol no primeiro tempo.", lambda g, r: S("ht_ou", line=1.5, over=True))
bet("totoro", "Totoro", "Meu Amigo Totoro", "Empate no intervalo", P, "Empate no primeiro tempo.", lambda g, r: S("ht_1x2", sel="draw"))
bet("smith", "Agente Smith", "Matrix", "Favorito vence o 1º tempo", P, "Favorito ganha o primeiro tempo.", lambda g, r: S("ht_1x2", sel=fav(g)))
bet("vader", "Darth Vader", "Star Wars", "Domínio total", "Intervalo/Final", "Favorito vence o 1º tempo e o jogo, com 70% ou mais.", lambda g, r: S("htft", ht=fav(g), ft=fav(g)) if pf(g) >= .70 else None)
bet("kiki", "Kiki", "O Serviço de Entregas da Kiki", "Empate e virada do favorito", "Intervalo/Final", "Empate no intervalo e vitória do favorito no fim.", lambda g, r: S("htft", ht="draw", ft=fav(g)))
bet("ash", "Ash Ketchum", "Pokémon", "2º tempo mais quente", "Tempo com mais gols", "O segundo tempo tem mais gols que o primeiro.", lambda g, r: S("sh_more"))
bet("chihiro", "Chihiro", "A Viagem de Chihiro", "1º tempo mais quente", "Tempo com mais gols", "O primeiro tempo tem mais gols que o segundo.", lambda g, r: S("fh_more"))

K = "Escanteios"
bet("gon", "Gon", "Hunter x Hunter", "Over 9.5 escanteios", K, "Mais de 9,5 escanteios.", lambda g, r: S("corners_ou", line=9.5, over=True))
bet("kurapika", "Kurapika", "Hunter x Hunter", "Over 8.5 escanteios", K, "Mais de 8,5 escanteios.", lambda g, r: S("corners_ou", line=8.5, over=True))
bet("leorio", "Leorio", "Hunter x Hunter", "Over 10.5 escanteios", K, "Mais de 10,5 escanteios.", lambda g, r: S("corners_ou", line=10.5, over=True))
bet("hisoka", "Hisoka", "Hunter x Hunter", "Over 11.5 escanteios", K, "Mais de 11,5 escanteios.", lambda g, r: S("corners_ou", line=11.5, over=True))
bet("killua", "Killua", "Hunter x Hunter", "Under 10.5 escanteios", K, "Menos de 10,5 escanteios.", lambda g, r: S("corners_ou", line=10.5, over=False))
bet("netero", "Netero", "Hunter x Hunter", "Under 9.5 escanteios", K, "Menos de 9,5 escanteios.", lambda g, r: S("corners_ou", line=9.5, over=False))
bet("meruem", "Meruem", "Hunter x Hunter", "Under 11.5 escanteios", K, "Menos de 11,5 escanteios.", lambda g, r: S("corners_ou", line=11.5, over=False))
bet("alluka", "Alluka", "Hunter x Hunter", "Escanteios pelo modelo", K, "Over 9,5 se o modelo esperar mais de 10 escanteios; senão under 9,5.", lambda g, r: S("corners_ou", line=9.5, over=g["model"]["esc"] > 10))
bet("mikasa", "Mikasa", "Attack on Titan", "Favorito pressiona", "Escanteios por time", "Favorito tem mais escanteios.", lambda g, r: S("corners_1x2", sel=fav(g)))
bet("armin", "Armin", "Attack on Titan", "Azarão pressiona", "Escanteios por time", "Azarão tem mais escanteios (time atrás no placar ataca mais).", lambda g, r: S("corners_1x2", sel=dog(g)))
bet("erwin", "Erwin", "Attack on Titan", "Mandante pressiona", "Escanteios por time", "Mandante tem mais escanteios.", lambda g, r: S("corners_1x2", sel="home"))
bet("hange", "Hange", "Attack on Titan", "Handicap de escanteios -2.5", "Escanteios por time", "Favorito com 3+ escanteios a mais, com 60% ou mais de chance de vencer.", lambda g, r: S("corners_ah", sel=fav(g), line=-2.5) if pf(g) >= .60 else None)
bet("reiner", "Reiner", "Attack on Titan", "Favorito 6+ escanteios", "Escanteios por time", "Favorito cobra mais de 5,5 escanteios.", lambda g, r: S("team_corners", sel=fav(g), line=5.5, over=True))
bet("annie", "Annie", "Attack on Titan", "Azarão 4+ escanteios", "Escanteios por time", "Azarão cobra mais de 3,5 escanteios.", lambda g, r: S("team_corners", sel=dog(g), line=3.5, over=True))

X = "Cartões"
bet("levi", "Levi", "Attack on Titan", "Over 4.5 cartões", X, "Mais de 4,5 cartões.", lambda g, r: S("cards_ou", line=4.5, over=True))
bet("zeke", "Zeke", "Attack on Titan", "Over 3.5 cartões", X, "Mais de 3,5 cartões.", lambda g, r: S("cards_ou", line=3.5, over=True))
bet("connie", "Connie", "Attack on Titan", "Over 5.5 cartões", X, "Mais de 5,5 cartões.", lambda g, r: S("cards_ou", line=5.5, over=True))
bet("historia", "Historia", "Attack on Titan", "Under 3.5 cartões", X, "Menos de 3,5 cartões.", lambda g, r: S("cards_ou", line=3.5, over=False))
bet("jean", "Jean", "Attack on Titan", "Under 4.5 cartões", X, "Menos de 4,5 cartões.", lambda g, r: S("cards_ou", line=4.5, over=False))
bet("eren", "Eren", "Attack on Titan", "Clássico pegado", X, "Mais de 3,5 cartões só em jogo equilibrado.", lambda g, r: S("cards_ou", line=3.5, over=True) if gap(g) < .20 else None)
bet("sasha", "Sasha", "Attack on Titan", "Jogo resolvido, pouco cartão", X, "Menos de 4,5 cartões quando o favorito tem 70% ou mais.", lambda g, r: S("cards_ou", line=4.5, over=False) if pf(g) >= .70 else None)

bet("tom", "Tom", "Tom & Jerry", "Gols ímpar", "Par ou ímpar", "Total de gols ímpar.", lambda g, r: S("oe", odd=True))
bet("jerry", "Jerry", "Tom & Jerry", "Gols par", "Par ou ímpar", "Total de gols par (0 conta como par).", lambda g, r: S("oe", odd=False))

O = "Faixa de odd"
bet("scooby", "Scooby-Doo", "Scooby-Doo", "Odd entre 1.30 e 1.60", O, "Entre todos os mercados do jogo, pega o mais provável com odd de 1,30 a 1,60.", lambda g, r: odds_pick(g, 1.30, 1.60))
bet("salsicha", "Salsicha", "Scooby-Doo", "Odd perto de 2.00", O, "O mercado com odd mais próxima de 2,00 (entre 1,85 e 2,20).", lambda g, r: odds_pick(g, 1.85, 2.20, target=2.0))
bet("fred", "Fred", "Scooby-Doo", "Odd alta, 4.00+", O, "O mercado mais provável com odd de 4,00 a 10,00.", lambda g, r: odds_pick(g, 4.0, 10.0))
bet("velma", "Velma", "Scooby-Doo", "Odd 1.60 a 1.90", O, "O mercado mais provável com odd de 1,60 a 1,90.", lambda g, r: odds_pick(g, 1.60, 1.90))

Z = "Controle aleatório"
bet("homer", "Homer Simpson", "Os Simpsons", "Aleatório", Z, "Escolhe ao acaso entre resultado, gols, ambas marcam e escanteios. É a régua.", lambda g, r: rnd_pick(g, r))
bet("patrick", "Patrick Estrela", "Bob Esponja", "Aleatório amplo", Z, "Ao acaso, incluindo handicap, cartões e 1º tempo.", lambda g, r: rnd_pick(g, r, wide=True))

M = "Gestão de banca"
bet("patinhas", "Tio Patinhas", "DuckTales", "Favorito, stake de 1%", M, "Favorito puro, apostando só R$ 10 (1% da banca inicial).", lambda g, r: S("1x2", sel=fav(g)), stake="flat1")
bet("donald", "Pato Donald", "Disney", "Favorito, 10% da banca", M, "Favorito puro, apostando 10% da banca atual em cada jogo.", lambda g, r: S("1x2", sel=fav(g)), stake="pct10")
bet("pateta", "Pateta", "Disney", "Martingale no Over 1.5", M, "Over 1,5 gol. Depois de cada erro dobra a aposta; ao acertar volta a R$ 20.", lambda g, r: S("ou", line=1.5, over=True), stake="martingale")
bet("mickey", "Mickey", "Disney", "Fibonacci na dupla chance", M, "Dupla chance do favorito. Stake sobe pela sequência de Fibonacci após erros e volta duas casas ao acertar.", lambda g, r: S("dc", sel=fav(g)), stake="fibonacci")
bet("bobesponja", "Bob Esponja", "Bob Esponja", "Ambas marcam, stake de 5%", M, "Ambas marcam, apostando R$ 50 (5% da banca inicial).", lambda g, r: S("btts", yes=True), stake="flat5")

V = "Leitura de mercado"
def _mov(g):
    r = g.get("real") or {}
    if r.get("abre_casa") and r.get("odd_casa") and r.get("abre_fora") and r.get("odd_fora"):
        return {"home": float(r["odd_casa"]) / float(r["abre_casa"]) - 1, "away": float(r["odd_fora"]) / float(r["abre_fora"]) - 1}
    return None
def _val(g):
    r = g.get("real") or {}
    if not (r.get("odd_casa") and r.get("odd_empate") and r.get("odd_fora")): return None
    w = g["model"]["w"]; best = None
    for s, k, p in (("home", "odd_casa", w[0]), ("draw", "odd_empate", w[1]), ("away", "odd_fora", w[2])):
        ev = float(r[k]) * p
        if best is None or ev > best[1]: best = (s, ev)
    return best
def _rl(g, k):
    r = g.get("real") or {}; v = r.get(k); return float(v) if v is not None else None
F = lambda g: g.get("feat")
bet("kurama", "Kurama", "Naruto", "Segue o dinheiro", V, "Quando a odd de um time cai 8% ou mais da abertura ao fechamento (dinheiro grande entrando), aposta nele.",
    lambda g, r: (lambda m: S("1x2", sel=next((s for s in ("home", "away") if m[s] <= -0.08), None)) if m and any(m[s] <= -0.08 for s in ("home", "away")) else None)(_mov(g)))
bet("jiraiya", "Jiraiya", "Naruto", "Contra o movimento", V, "Quando a odd de um time sobe 8% ou mais (mercado abandonou), aposta nele.",
    lambda g, r: (lambda m: S("1x2", sel=next((s for s in ("home", "away") if m[s] >= 0.08), None)) if m and any(m[s] >= 0.08 for s in ("home", "away")) else None)(_mov(g)))
bet("estranho", "Doutor Estranho", "Marvel", "Caçador de valor", V, "Compara a odd da casa com a probabilidade de referência do mercado; aposta quando a casa paga 3% ou mais acima do justo.",
    lambda g, r: (lambda v: S("1x2", sel=v[0]) if v and v[1] >= 1.03 else None)(_val(g)))
bet("wanda", "Wanda", "Marvel", "Valor no over/under", V, "Aposta no over ou under 2,5 quando a melhor odd do mercado supera a referência em 3%+. Ao vivo só funciona com odds de várias casas.",
    lambda g, r: None)
bet("hermione", "Hermione", "Harry Potter", "Handicap grande no azarão", V, "Quando a linha real de handicap é de 1 gol ou mais, aposta no azarão com a linha.",
    lambda g, r: (lambda l: S("ah", sel="away" if l < 0 else "home", line=abs(l)) if l is not None and abs(l) >= 1 else None)(_rl(g, "handicap_casa")))
bet("rony", "Rony", "Harry Potter", "Handicap curto do mandante", V, "Mandante favorito com linha real de −0,25 ou −0,5: aposta nele.",
    lambda g, r: (lambda l: S("ah", sel="home", line=l) if l in (-0.25, -0.5) and fav(g) == "home" else None)(_rl(g, "handicap_casa")))
bet("snape", "Snape", "Harry Potter", "Over contra o mercado", V, "Over 2,5 só quando a odd real é 2,00 ou mais (mercado espera jogo fechado).",
    lambda g, r: S("ou", line=2.5, over=True) if _rl(g, "linha_gols") == 2.5 and (_rl(g, "odd_over") or 0) >= 2.0 else None)
bet("dumbledore", "Dumbledore", "Harry Potter", "Under contra o mercado", V, "Under 2,5 só quando a odd real é 2,10 ou mais.",
    lambda g, r: S("ou", line=2.5, over=False) if _rl(g, "linha_gols") == 2.5 and (_rl(g, "odd_under") or 0) >= 2.1 else None)
FM = "Forma recente"
bet("kame", "Mestre Kame", "Dragon Ball", "Time descansado", FM, "Time com 3+ dias a mais de descanso, se não for azarão forte: empate anula nele. Ao vivo fica estudando enquanto não houver datas confiáveis.",
    lambda g, r: None)
bet("cell", "Cell", "Dragon Ball", "Cansaço do adversário", FM, "Time que jogou há 3 dias ou menos contra adversário descansado: aposta que o descansado não perde. Ao vivo fica estudando.",
    lambda g, r: None)
bet("satan", "Mr. Satan", "Dragon Ball", "Embalado contra quem está mal", FM, "Time com 2,2+ pontos por jogo nos últimos 5 contra time com 1,0 ou menos: vitória do embalado.",
    lambda g, r: (lambda f: S("1x2", sel="home") if f and f["h"]["ppg5"] >= 2.2 and f["a"]["ppg5"] <= 1.0 else (S("1x2", sel="away") if f and f["a"]["ppg5"] >= 2.2 and f["h"]["ppg5"] <= 1.0 else None))(F(g)))
bet("frieza", "Freeza", "Dragon Ball", "Regressão à média", FM, "Favorito em fase ruim (0,8 ponto por jogo ou menos nos últimos 5): o mercado exagera a crise, aposta nele.",
    lambda g, r: (lambda f: S("1x2", sel=fav(g)) if f and pf(g) >= .5 and f["h" if fav(g) == "home" else "a"]["ppg5"] <= 0.8 else None)(F(g)))
bet("kuririn", "Kuririn", "Dragon Ball", "Muito chute no alvo", FM, "Os dois times somam 10+ chutes no alvo por jogo nos últimos 5: over 2,5.",
    lambda g, r: (lambda f: S("ou", line=2.5, over=True) if f and f["h"]["sot5"] + f["a"]["sot5"] >= 10 else None)(F(g)))
bet("yamcha", "Yamcha", "Dragon Ball", "Ataques secos", FM, "Os dois somam 7 ou menos chutes no alvo por jogo: under 2,5.",
    lambda g, r: (lambda f: S("ou", line=2.5, over=False) if f and f["h"]["sot5"] + f["a"]["sot5"] <= 7 else None)(F(g)))
bet("tenshinhan", "Tenshinhan", "Dragon Ball", "Escanteios pela forma", FM, "Soma a média de escanteios a favor e contra dos dois times nos últimos 5: over 9,5 se der 11+, under 9,5 se der 8,5 ou menos.",
    lambda g, r: (lambda f: None if not f else (lambda e2: S("corners_ou", line=9.5, over=True) if e2 >= 11 else (S("corners_ou", line=9.5, over=False) if e2 <= 8.5 else None))((f["h"]["cf5"] + f["a"]["ca5"] + f["a"]["cf5"] + f["h"]["ca5"]) / 2))(F(g)))
bet("pieck", "Pieck", "Attack on Titan", "Azarão que cobra escanteio", FM, "Versão estudada do Armin: azarão mais escanteios só se ele cobra 5,5+ escanteios por jogo nos últimos 5.",
    lambda g, r: (lambda f: S("corners_1x2", sel=dog(g)) if f and f["h" if dog(g) == "home" else "a"]["cf5"] >= 5.5 else None)(F(g)))
bet("chichi", "Chi-Chi", "Dragon Ball", "Times faltosos", FM, "Os dois times somam 5+ cartões por jogo nos últimos 5: over 4,5 cartões.",
    lambda g, r: (lambda f: S("cards_ou", line=4.5, over=True) if f and f["h"]["k5"] + f["a"]["k5"] >= 5 else None)(F(g)))
bet("videl", "Videl", "Dragon Ball", "Times disciplinados", FM, "Os dois somam 3,5 ou menos cartões por jogo nos últimos 5: under 3,5 cartões.",
    lambda g, r: (lambda f: S("cards_ou", line=3.5, over=False) if f and f["h"]["k5"] + f["a"]["k5"] <= 3.5 else None)(F(g)))
bet("lupin", "Lupin", "Lupin III", "Menos cartões na Serie A", L_, "Pista do histórico: cartões caíram na Serie A. Under 4,5 cartões em todo jogo da Serie A.",
    lambda g, r: S("cards_ou", line=4.5, over=False) if lg(g) == "serie_a" else None)
bet("sakura", "Sakura", "Naruto", "Luta contra o rebaixamento", "Calendário", "De abril em diante, time da zona de rebaixamento em casa contra time de meio de tabela: mandante não perde.",
    lambda g, r: S("dc", sel="home") if 4 <= int(g["kickoff"][5:7]) <= 6 and g["home_rank"] >= nteams(g) - 3 and 4 < g["away_rank"] < nteams(g) - 3 else None)
bet("kevin", "Kevin", "Esqueceram de Mim", "Azarão em casa", "Calendário", "Visitante favorito, mas mandante com 30%+ de chance: empate anula no mandante.",
    lambda g, r: S("dnb", sel="home") if fav(g) == "away" and pd_(g) >= .3 else None)

# ---------- personagens do Estúdio que também apostam ao vivo (testados em 12 ligas, ver ESTUDIO.md) ----------
EA = "Estúdio ao vivo"
def _w(g, sel): return {"home": g["model"]["w"][0], "draw": g["model"]["w"][1], "away": g["model"]["w"][2]}[sel]
def _pc(g, s):
    c = calib_prob(g, s)
    return c if c is not None else price(g, s)[0]
def _hyuga(g):
    best = None
    for sel in ("home", "draw", "away"):
        s = S("1x2", sel=sel); o = price(g, s)[1]; ev = o * _w(g, sel)
        if o >= 2.0 and ev >= 1.0 and (best is None or ev > best[0]): best = (ev, s)
    return best[1] if best else None
def _ishizaki(g):
    c = [(_w(g, sel), S("1x2", sel=sel)) for sel in ("home", "draw", "away") if price(g, S("1x2", sel=sel))[1] >= 3.0]
    return max(c, key=lambda x: x[0])[1] if c else None
def _tsubasa(g):
    r = g.get("real") or {}; c = []
    if r.get("linha_gols") is not None:
        for ov in (True, False): c.append(S("ou", line=float(r["linha_gols"]), over=ov))
    if r.get("handicap_casa") is not None:
        hl = float(r["handicap_casa"]); c += [S("ah", sel="home", line=hl), S("ah", sel="away", line=-hl)]
    best = None
    for s in c:
        if not real_odd(g, s): continue
        p, o = _pc(g, s), price(g, s)[1]
        if .45 <= p <= .55 and o >= 2.0 and (best is None or p * o > best[0]): best = (p * o, s)
    return best[1] if best else None
bet("wakabayashi", "Genzo Wakabayashi", "Captain Tsubasa", "Favorito de 60%+", EA, "Vitória do favorito com 60% ou mais. No Estúdio provou lucro só no Brasileirão, Portugal, Serie A e Premier League; no resto estuda.",
    lambda g, r: S("1x2", sel=fav(g)) if pf(g) >= .60 else None)
bet("gouenji", "Shuuya Gouenji", "Inazuma Eleven", "Over 2,5 provável", EA, "Mais de 2,5 gols quando a chance passa de 55%.",
    lambda g, r: S("ou", line=2.5, over=True) if _pc(g, S("ou", line=2.5, over=True)) >= .55 else None)
bet("endou", "Mamoru Endou", "Inazuma Eleven", "Under 2,5 provável", EA, "Menos de 2,5 gols quando a chance passa de 50%.",
    lambda g, r: S("ou", line=2.5, over=False) if _pc(g, S("ou", line=2.5, over=False)) >= .50 else None)
bet("misaki", "Taro Misaki", "Captain Tsubasa", "Mandante equilibrado", EA, "Vitória do mandante quando a chance dele fica entre 40% e 55%.",
    lambda g, r: S("1x2", sel="home") if .40 <= _w(g, "home") < .55 else None)
bet("chigiri", "Hyoma Chigiri", "Blue Lock", "Visitante quase 50/50", EA, "Vitória do visitante azarão quando ele tem entre 40% e 50%. No Estúdio provou lucro na Serie A, La Liga, Portugal e Premier League.",
    lambda g, r: S("1x2", sel="away") if fav(g) == "home" and .40 <= _w(g, "away") < .50 else None)
bet("hyuga", "Kojiro Hyuga", "Captain Tsubasa", "Odd 2+ com valor", EA, "Resultado com odd real de 2,00 ou mais em que a odd × a chance justa do mercado de previsão passa de 1 (paga mais do que o risco).", lambda g, r: _hyuga(g))
bet("ishizaki", "Ryo Ishizaki", "Captain Tsubasa", "Odd 3+ sem olhar valor", EA, "O resultado mais provável entre os que pagam 3,00 ou mais. É a régua do \"odd alta\": no Estúdio perdeu 10%.", lambda g, r: _ishizaki(g))
bet("tsubasa", "Tsubasa Ozora", "Captain Tsubasa", "50/50 pagando 2+", EA, "A ideia do LoL: gols ou handicap com odd real da DraftKings de 2,00 ou mais num lado que a calibração dá entre 45% e 55%.", lambda g, r: _tsubasa(g))
bet("nagi", "Seishiro Nagi", "Blue Lock", "Empate com valor", EA, "Empate quando a odd real × a chance justa do mercado de previsão passa de 1,03.",
    lambda g, r: S("1x2", sel="draw") if price(g, S("1x2", sel="draw"))[1] * _w(g, "draw") >= 1.03 else None)
bet("bachira", "Meguru Bachira", "Blue Lock", "Favorito com valor", EA, "Favorito quando a odd real × a chance justa do mercado de previsão passa de 1,02.",
    lambda g, r: S("1x2", sel=fav(g)) if price(g, S("1x2", sel=fav(g)))[1] * pf(g) >= 1.02 else None)

# ---------- mercados de minutos e de chutes ----------
MC = "Minutos e chutes"
_sh = lambda g: SHOTS.get(g["league"], SHOTS["_"])
bet("roronoa_ace", "Portgas D. Ace", "One Piece", "Gol relâmpago", MC, "Gol até os 15 minutos, quando o jogo promete gols (3+ esperados).",
    lambda g, r: S("goal_before", min=15, yes=True) if xg(g) >= 3.0 else None)
bet("kaneda", "Kaneda", "Akira", "Gol até os 30", MC, "Pelo menos um gol nos primeiros 30 minutos.", lambda g, r: S("goal_before", min=30, yes=True))
bet("lain", "Lain", "Serial Experiments Lain", "Começo travado", MC, "Nenhum gol até os 30 minutos em jogo de poucos gols esperados (2,4 ou menos).",
    lambda g, r: S("goal_before", min=30, yes=False) if xg(g) <= 2.4 else None)
bet("spike", "Spike Spiegel", "Cowboy Bebop", "Gol antes do intervalo cedo", MC, "Gol até os 20 minutos quando o favorito tem 65%+.",
    lambda g, r: S("goal_before", min=20, yes=True) if pf(g) >= .65 else None)
bet("mob", "Mob", "Mob Psycho 100", "Muitos chutes", MC, "Mais de 24,5 chutes no jogo nas ligas que mais finalizam (Premier, Bundesliga, Eredivisie) ou com 3+ gols esperados.",
    lambda g, r: S("shots_ou", line=24.5, over=True) if lg(g) in ("epl", "bundesliga", "eredivisie") or xg(g) >= 3.0 else None)
bet("reigen", "Reigen Arataka", "Mob Psycho 100", "Poucos chutes", MC, "Menos de 24,5 chutes em La Liga, Portugal e Serie A, quando o jogo promete poucos gols.",
    lambda g, r: S("shots_ou", line=24.5, over=False) if lg(g) in ("la_liga", "primeira", "serie_a") and xg(g) <= 2.6 else None)
bet("deku", "Deku", "My Hero Academia", "Chutes no alvo, linha 8,5", MC, "Mais de 8,5 chutes no alvo quando a calibração dá 50%+.",
    lambda g, r: S("sot_ou", line=8.5, over=True) if price(g, S("sot_ou", line=8.5, over=True))[0] >= .50 else None)
bet("bakugo", "Bakugo", "My Hero Academia", "Explosão no alvo", MC, "Mais de 9,5 chutes no alvo em jogo aberto (2,9+ gols esperados).",
    lambda g, r: S("sot_ou", line=9.5, over=True) if xg(g) >= 2.9 else None)
bet("todoroki", "Todoroki", "My Hero Academia", "Pontaria fria", MC, "Menos de 7,5 chutes no alvo quando a calibração dá menos de 55% para o over.",
    lambda g, r: S("sot_ou", line=7.5, over=False) if price(g, S("sot_ou", line=7.5, over=True))[0] < .55 else None)

# ---------- outros esportes (vencedor do jogo; odd = probabilidade Sportradar + margem, ou DraftKings quando houver) ----------
NBA_, NHL_, NFL_ = "NBA", "NHL", "NFL"
ml = lambda sel: S("ml", sel=sel)
bet("sakuragi", "Sakuragi", "Slam Dunk", "Azarão NBA abaixo de 30%", NBA_, "Azarão com menos de 30% de chance. No estudo de 8 mil jogos perdeu 17%: fica para provar se o estudo estava certo.", lambda g, r: ml(dog(g)) if pd_(g) < .30 else None, sport="nba")
bet("akagi", "Akagi", "Slam Dunk", "Favorito NBA 75%+", NBA_, "Vencedor do jogo quando o favorito tem 75% ou mais.", lambda g, r: ml(fav(g)) if pf(g) >= .75 else None, sport="nba")
bet("rukawa", "Rukawa", "Slam Dunk", "Favorito NBA 55–75%", NBA_, "Favorito médio, entre 55% e 75%: odd entre 1,3 e 1,8.", lambda g, r: ml(fav(g)) if .55 <= pf(g) < .75 else None, sport="nba")
bet("sendoh", "Sendoh", "Slam Dunk", "Quase 50/50 NBA", NBA_, "A ideia do LoL: jogo quase parelho, aposta no azarão de 40–50%, odd perto de 2,1.", lambda g, r: ml(dog(g)) if .40 <= pd_(g) < .50 else None, sport="nba")
bet("mitsui", "Mitsui", "Slam Dunk", "Mandante NBA", NBA_, "Sempre o time da casa.", lambda g, r: ml("home"), sport="nba")
bet("aomine", "Aomine", "Kuroko no Basket", "Favorito visitante NBA", NBA_, "Favorito jogando fora de casa (o mercado costuma pesar demais o mando).", lambda g, r: ml("away") if fav(g) == "away" else None, sport="nba")
bet("shoyo", "Hinata Shoyo", "Haikyuu", "Favorito NHL 55%+", NHL_, "Vencedor no hóquei quando o favorito tem 55% ou mais.", lambda g, r: ml(fav(g)) if pf(g) >= .55 else None, sport="nhl")
bet("kageyama", "Kageyama", "Haikyuu", "Quase 50/50 NHL", NHL_, "Hóquei é muito parelho: azarão entre 40% e 50%, odd acima de 2.", lambda g, r: ml(dog(g)) if .40 <= pd_(g) < .50 else None, sport="nhl")
bet("oikawa", "Oikawa", "Haikyuu", "Mandante NHL", NHL_, "Sempre o time da casa no hóquei.", lambda g, r: ml("home"), sport="nhl")
bet("tsukishima", "Tsukishima", "Haikyuu", "Visitante NHL", NHL_, "Sempre o visitante no hóquei.", lambda g, r: ml("away"), sport="nhl")
bet("sena", "Sena", "Eyeshield 21", "Azarão NFL 35–50%", NFL_, "Azarão que não é zebra grande: 35% a 50% de chance.", lambda g, r: ml(dog(g)) if .35 <= pd_(g) < .50 else None, sport="nfl")
bet("hiruma", "Hiruma", "Eyeshield 21", "Favorito NFL 70%+", NFL_, "Favorito forte na NFL.", lambda g, r: ml(fav(g)) if pf(g) >= .70 else None, sport="nfl")
bet("shin", "Shin", "Eyeshield 21", "Favorito NFL 55–70%", NFL_, "Favorito médio na NFL.", lambda g, r: ml(fav(g)) if .55 <= pf(g) < .70 else None, sport="nfl")
bet("kurita", "Kurita", "Eyeshield 21", "Mandante NFL", NFL_, "Sempre o time da casa na NFL.", lambda g, r: ml("home"), sport="nfl")
bet("agon", "Agon", "Eyeshield 21", "Zebra NFL abaixo de 30%", NFL_, "Zebra grande, odd acima de 3.", lambda g, r: ml(dog(g)) if pd_(g) < .30 else None, sport="nfl")


ACCAS = [
 {"id": "jack", "nome": "Jack Sparrow", "origem": "Piratas do Caribe", "estrategia": "Múltipla de favoritos", "mercado": "Múltipla",
  "regra": "Uma múltipla por dia com todos os favoritos de 70% ou mais."},
 {"id": "barbossa", "nome": "Barbossa", "origem": "Piratas do Caribe", "estrategia": "Múltipla de over 1.5", "mercado": "Múltipla",
  "regra": "Uma múltipla por dia com os 4 jogos mais prováveis de ter mais de 1,5 gol."},
 {"id": "davyjones", "nome": "Davy Jones", "origem": "Piratas do Caribe", "estrategia": "Múltipla de ambas marcam", "mercado": "Múltipla",
  "regra": "Uma múltipla por dia com os 3 jogos mais prováveis de ambos marcarem."},
]
BY = {b["id"]: b for b in B}

def portmanteau(a, b):
    x, y = BY[a]["nome"].split()[0], BY[b]["nome"].split()[-1]
    return x[:max(2, (len(x) + 1) // 2)] + y[len(y) // 2:].lower()

def fusion_entry(a, b, nome=None):
    A, Bb = BY[a], BY[b]
    return {"id": f"fus_{a}_{b}", "nome": nome or portmanteau(a, b), "origem": f"Fusão de {A['nome']} e {Bb['nome']}",
            "estrategia": f"{A['estrategia']} + {Bb['estrategia']}", "mercado": "Fusão", "stake": "half", "a": a, "b": b,
            "regra": f"Só entra nos jogos em que {A['nome']} e {Bb['nome']} apostariam ao mesmo tempo, e faz as duas apostas com R$ 10 cada."}


# ---------- aprendizado: cada apostador decide onde apostar e quanto, a partir do histórico + resultados ao vivo ----------
def _lj(name):
    p = os.path.join(HERE, name)
    return json.load(open(p)) if os.path.exists(p) else {}
POLICY = _lj("policy.json")       # segmentos com histórico (liga, faixa de odd, n, apostado, lucro) — Supabase politica_json()
HISTORICO = _lj("historico.json") # resumo do backtest v2 por apostador
FORMA = _lj("forma.json")         # últimos 5 jogos por time (temporada atual) — Supabase forma_json()
SEM_PRECO = {"roronoa_ace", "kaneda", "lain", "spike", "light", "near", "mello", "rem", "bulma", "brook", "chopper", "genos", "itachi", "tony", "bang", "saitama", "sonic",
             "garou", "kakashi", "king", "fubuki", "shrek", "scooby", "salsicha", "fred", "velma"}
CONTROLES = {"homer", "patrick"}
BANDS = {"a": "odd até 1,50", "b": "odd 1,50–2,00", "c": "odd 2,00–3,00", "d": "odd 3,00–5,00", "e": "odd 5,00+"}
SDU = {"a": .55, "b": .87, "c": 1.18, "d": 1.67, "e": 2.45}
LG_NOME = {"nba": "NBA", "nhl": "NHL", "nfl": "NFL", "la_liga": "La Liga", "serie_a": "Serie A", "ligue_1": "Ligue 1", "bundesliga": "Bundesliga", "epl": "Premier League", "mls": "MLS", "brasileirao": "Brasileirão", "primeira": "Portugal", "eredivisie": "Eredivisie", "champions_league": "Champions League", "europa_league": "Europa League", "conference_league": "Conference League", "championship": "Championship", "serie_b": "Serie B (Itália)", "bundesliga_2": "2. Bundesliga", "la_liga_2": "La Liga 2", "j_league": "J-League", "liga_mx": "Liga MX", "colombia": "Colômbia", "escocia": "Escócia", "turquia": "Turquia", "belgica": "Bélgica", "argentina": "Argentina", "libertadores": "Libertadores", "sul_americana": "Sul-Americana", "austria": "Áustria", "dinamarca": "Dinamarca", "paraguai": "Paraguai", "equador": "Equador", "australia": "Austrália", "multi": "Múltiplas"}

def band_of(odd):
    return "a" if odd < 1.5 else "b" if odd < 2 else "c" if odd < 3 else "d" if odd < 5 else "e"

def seg_table(st, bid):
    """Junta histórico (POLICY) e resultados ao vivo (inclusive palpites de estudo) por liga x faixa de odd, em unidades de R$ 20."""
    T = {}
    for lgk, b, n, staked, lucro in POLICY.get(bid, []):
        T[(lgk, b)] = {"n": n, "st": staked, "lu": lucro, "hn": n}
    for p in st["picks"]:
        if p["bettor"] != bid or p.get("grade") is None or p.get("void"): continue
        lgk = "multi" if p.get("legs") else st["games"][p["game"]]["league"]
        b = band_of(p["odd"]); x = T.setdefault((lgk, b), {"n": 0, "st": 0, "lu": 0, "hn": 0})
        gr = p["grade"]
        x["n"] += 1; x["st"] += BASE; x["lu"] += BASE * (p["odd"] - 1) * gr if gr > 0 else BASE * gr
    for (lgk, b), x in T.items():
        x["roi"] = x["lu"] / x["st"] if x["st"] else 0
        x["post"] = (x["lu"] - 0.03 * BASE * 60) / (x["st"] + BASE * 60)
        x["z"] = x["roi"] / (SDU[b] / math.sqrt(max(1, x["n"])))
        x["ativo"] = bid not in SEM_PRECO and x["n"] >= 40 and x["post"] > 0 and x["z"] >= 1.0
    return T

def decide(st, bid, league, odd, _cache={}):
    """Retorna (stake_multiplicador, z, ativo). Controles aleatórios sempre apostam."""
    if bid in CONTROLES: return 1.0, None, True
    key = (id(st), bid)
    if key not in _cache: _cache[key] = seg_table(st, bid)
    x = _cache[key].get((league, band_of(odd)))
    if not x or not x["ativo"]: return 0.0, x["z"] if x else None, False
    return (0.5 if x["z"] < 1.5 else 1.0 if x["z"] < 2 else 2.0), x["z"], True

ALIAS = {"atletico de madrid": "Ath Madrid", "athletic club": "Ath Bilbao", "athletic bilbao": "Ath Bilbao", "nottingham forest": "Nott'm Forest",
         "borussia monchengladbach": "M'gladbach", "1 fc koln": "FC Koln", "fc koln": "FC Koln", "eintracht frankfurt": "Ein Frankfurt",
         "paris saint germain": "Paris SG", "espanyol": "Espanol", "real sociedad": "Sociedad", "rayo vallecano": "Vallecano",
         "deportivo la coruna": "La Coruna", "racing santander": "Santander", "manchester united": "Man United", "manchester city": "Man City",
         "inter milano": "Inter", "internazionale": "Inter", "ac milan": "Milan", "juventus turin": "Juventus", "bayer leverkusen": "Leverkusen",
         "celta de vigo": "Celta", "real betis": "Betis", "brighton": "Brighton", "wolverhampton": "Wolves", "fc barcelona": "Barcelona"}
def _team_form(league, name):
    import unicodedata as _u, difflib as _d
    n = _u.normalize("NFKD", name).encode("ascii", "ignore").decode().lower().replace("-", " ").replace(".", "").strip()
    cand = {k.split("|", 1)[1]: v for k, v in FORMA.items() if k.startswith(league + "|")}
    if not cand: return None
    t = ALIAS.get(n)
    if t not in cand:
        best = max(cand, key=lambda c: _d.SequenceMatcher(None, n, c.lower()).ratio())
        t = best if _d.SequenceMatcher(None, n, best.lower()).ratio() >= 0.6 else None
    if not t: return None
    v = cand[t]
    return {"ppg5": v[0], "gf5": v[1], "ga5": v[2], "sot5": v[3], "cf5": v[4], "ca5": v[5], "k5": v[6], "ultimo": v[7]}

def features(g):
    h, a = _team_form(g["league"], g["home_name"]), _team_form(g["league"], g["away_name"])
    g["feat"] = {"h": h, "a": a} if h and a else None
    return g["feat"]

def diario(st, bid):
    """Texto curto: o que o personagem aprendeu e o que está fazendo."""
    if bid in CONTROLES: return "Não aprende de propósito: aposta ao acaso para servir de régua."
    if bid in SEM_PRECO: return "Ainda não há odd real nem calibrada para o mercado dele. Segue só estudando, sem arriscar a banca, até o preço ficar confiável."
    T = seg_table(st, bid)
    at = sorted(((k, x) for k, x in T.items() if x["ativo"]), key=lambda kx: -kx[1]["z"])
    ao_vivo = sum(x["n"] - x["hn"] for x in T.values())
    if not at:
        quase = sorted(((k, x) for k, x in T.items() if x["n"] >= 20), key=lambda kx: -kx[1]["post"])[:1]
        msg = "Nenhuma faixa dele deu lucro com evidência suficiente. Está em modo teste: aposta 2% da banca atual dele, com dinheiro fictício, até encontrar onde vale."
        if quase:
            (lgk, b), x = quase[0]
            msg += f" A mais perto de virar: {LG_NOME.get(lgk, lgk)}, {BANDS[b]} ({x['roi']*100:+.1f}% em {x['n']} jogos)."
        return msg
    partes = [f"{LG_NOME.get(l, l)} com {BANDS[b]} ({x['roi']*100:+.1f}% em {x['n']}, {'aposta alta' if x['z'] >= 2 else 'aposta normal' if x['z'] >= 1.5 else 'aposta baixa'})" for (l, b), x in at[:4]]
    txt = "Aposta só onde o histórico provou lucro: " + "; ".join(partes) + (f" e mais {len(at) - 4}" if len(at) > 4 else "") + ". No resto, só estuda."
    if ao_vivo: txt += f" Já somou {ao_vivo} resultados ao vivo ao aprendizado."
    return txt

# ---------- estado ----------
def load():
    if os.path.exists(STATE): return json.load(open(STATE))
    return {"games": {}, "picks": [], "days": []}

def save(st): json.dump(st, open(STATE, "w"), ensure_ascii=False, indent=1)

def dublin_day(iso):
    t = datetime.datetime.fromisoformat(iso)
    return t.astimezone(datetime.timezone.utc).date().isoformat()  # Dublin = UTC em novembro

def add_games(path):
    st = load(); n = 0
    for g in json.load(open(path)):
        if g["id"] in st["games"]: continue
        g["day"] = dublin_day(g["kickoff"]); g["model"] = model(g); g["status"] = "scheduled"
        g["odds"] = odds_board(g)
        st["games"][g["id"]] = g; n += 1
        if g["day"] not in st["days"]: st["days"].append(g["day"]); st["days"].sort()
    save(st); print(n, "jogos novos")

def odds_board(g):
    f, d = fav(g), dog(g)
    if sport_of(g) != "futebol":
        return [["Vencedor", [[g["home_name"], price(g, S("ml", sel="home"))[1]], [g["away_name"], price(g, S("ml", sel="away"))[1]]]]]
    rows = [("Resultado", [("1", S("1x2", sel="home")), ("X", S("1x2", sel="draw")), ("2", S("1x2", sel="away"))]),
            ("Dupla chance", [("1X", S("dc", sel="home")), ("X2", S("dc", sel="away")), ("Empate anula fav.", S("dnb", sel=f))]),
            ("Gols", [(f"{'+' if o else '-'}{l}", S("ou", line=l, over=o)) for l in (1.5, 2.5, 3.5) for o in (True, False)]),
            ("Ambas marcam", [("Sim", S("btts", yes=True)), ("Não", S("btts", yes=False))]),
            ("Handicap", [("Fav. -1.5", S("ah", sel=f, line=-1.5)), ("Fav. -1", S("ah", sel=f, line=-1.0)), ("Fav. -0.5", S("ah", sel=f, line=-0.5)),
                          ("Az. +0.5", S("ah", sel=d, line=0.5)), ("Az. +1", S("ah", sel=d, line=1.0)), ("Az. +1.5", S("ah", sel=d, line=1.5))]),
            ("Gols do mandante", [("+0.5", S("team_ou", sel="home", line=0.5, over=True)), ("+1.5", S("team_ou", sel="home", line=1.5, over=True)), ("+2.5", S("team_ou", sel="home", line=2.5, over=True))]),
            ("Gols do visitante", [("+0.5", S("team_ou", sel="away", line=0.5, over=True)), ("+1.5", S("team_ou", sel="away", line=1.5, over=True)), ("+2.5", S("team_ou", sel="away", line=2.5, over=True))]),
            ("Escanteios", [(f"{'+' if o else '-'}{l}", S("corners_ou", line=l, over=o)) for l in (8.5, 9.5, 10.5, 11.5) for o in (True, False)]),
            ("Cartões", [(f"{'+' if o else '-'}{l}", S("cards_ou", line=l, over=o)) for l in (3.5, 4.5) for o in (True, False)])]
    out = [[grp, [[lab, price(g, s)[1]] for lab, s in items]] for grp, items in rows]
    r = g.get("real")
    if r:
        dk = [[l, float(r[k])] for l, k in (("1", "odd_casa"), ("X", "odd_empate"), ("2", "odd_fora")) if r.get(k)]
        if r.get("linha_gols") is not None:
            dk += [[f"+{r['linha_gols']}", float(r["odd_over"])], [f"-{r['linha_gols']}", float(r["odd_under"])]]
        if r.get("handicap_casa") is not None and r.get("odd_hcp_casa"):
            hl = float(r["handicap_casa"]); dk += [[f"Casa {hl:+g}", float(r["odd_hcp_casa"])], [f"Fora {-hl:+g}", float(r["odd_hcp_fora"])]]
        out.insert(0, ["DraftKings (odd real)", dk])
    return out

import unicodedata, difflib
def _norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    for w in ("fc ", " fc", "cf ", " cf", "ac ", "ssc ", "as ", "sc ", " sc", "1. ", "afc ", "calcio", "club", "de ", "turin", "milano", "hove albion"):
        s = s.replace(w, " ")
    return " ".join(s.split())

def attach_odds(path):
    """Liga odds reais (linhas de jogos_resumo do Supabase) aos jogos pelo horário e nomes dos times."""
    st = load(); rows = json.load(open(path)); n = repriced = 0
    now = datetime.datetime.now(datetime.timezone.utc)
    for g in st["games"].values():
        k = datetime.datetime.fromisoformat(g["kickoff"])
        best = None
        for r in rows:
            if r.get("odd_casa") is None and r.get("odd_over") is None: continue
            rk = datetime.datetime.fromisoformat(str(r["kickoff"]).replace(" ", "T").replace("+00", "+00:00") if str(r["kickoff"]).endswith("+00") else str(r["kickoff"]).replace(" ", "T"))
            if abs((rk - k).total_seconds()) > 900: continue
            sc = difflib.SequenceMatcher(None, _norm(g["home_name"]), _norm(r["home"])).ratio() + \
                 difflib.SequenceMatcher(None, _norm(g["away_name"]), _norm(r["away"])).ratio()
            if sc > 1.1 and (best is None or sc > best[0]): best = (sc, r)
        if not best: continue
        r = best[1]
        g["real"] = {kk: r.get(kk) for kk in ("odd_casa", "odd_empate", "odd_fora", "linha_gols", "odd_over", "odd_under", "handicap_casa", "odd_hcp_casa", "odd_hcp_fora", "abre_casa", "abre_fora")}
        g["real"]["espn_id"] = r.get("id"); g["odds"] = odds_board(g); n += 1
        if k <= now: continue
        for p in st["picks"]:
            if p.get("game") == g["id"] and p["grade"] is None:
                ro = real_odd(g, p["spec"])
                if ro:
                    p["odd"] = round(float(ro), 2); p["fonte"] = "DraftKings"; repriced += 1
    save(st); print(n, "jogos com odds reais;", repriced, "palpites repreçados")

def bankroll(st, bid):
    return BANCA + sum(p["pts"] for p in st["picks"] if p["bettor"] == bid and p.get("pts") is not None)

def stake_for(st, b):
    kind = b.get("stake", "flat") if isinstance(b, dict) else "flat"
    bank = bankroll(st, b["id"])
    if bank < 1: return 0
    hist = [p for p in st["picks"] if p["bettor"] == b["id"] and p.get("grade") is not None and not p.get("void")]
    hist.sort(key=lambda p: (p["day"], p.get("kickoff", "")))
    if kind == "flat1": s = 10
    elif kind == "flat5": s = 50
    elif kind == "pct10": s = round(bank * .10, 2)
    elif kind == "martingale":
        s = BASE
        for p in hist: s = BASE if p["grade"] > 0 else (s * 2 if p["grade"] < 0 else s)
    elif kind == "fibonacci":
        fib = [1, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89]; i = 0
        for p in hist: i = max(0, i - 2) if p["grade"] > 0 else (min(len(fib) - 1, i + 1) if p["grade"] < 0 else i)
        s = 10 * fib[i]
    else: s = BASE
    return round(min(s, bank), 2)

FALENCIA = 10.0   # banca abaixo disso = faliu (morre; pode renascer como personagem novo)
TESTE = 0.02      # fora das faixas confiáveis: aposta 2% da banca (aprende arriscando)

def _livre(st, bid):
    """Banca atual menos o que já está em jogo (pendente)."""
    bank = bankroll(st, bid)
    pend = sum(p.get("stake", 0) for p in st["picks"] if p["bettor"] == bid and p.get("grade") is None)
    return bank, bank - pend

def falido(st, bid):
    return bankroll(st, bid) < FALENCIA

def seg_edge(st, bid, league, odd, _c={}):
    """Vantagem estimada (lucro esperado por real apostado, já descontada a sorte) e amostra do segmento liga x faixa de odd."""
    k = (id(st), bid)
    if k not in _c: _c[k] = seg_table(st, bid)
    x = _c[k].get((league, band_of(odd)))
    return (x["post"], x["n"], x["ativo"]) if x else (None, 0, False)

def stake_of(st, b, mult, ativo, cap=0.05, gest=None, odd=None, edge=None):
    """Todo palpite arrisca dinheiro. Confiante: R$10–40 (até 5%). Testando: 2% da banca. Gestão de banca: a regra dele (até 10%)."""
    bid = b["id"]
    bank, livre = _livre(st, bid)
    if bank < FALENCIA or livre <= 0: return 0.0
    kind = b.get("stake", "flat")
    if bid in CONTROLES: s = BASE
    elif kind not in ("flat", "half"): s = min(gest if gest is not None else stake_for(st, b), bank * 0.10)
    elif ativo and edge and edge > 0 and odd and odd > 1:
        # Kelly fracionado (1/4): aposta proporcional à vantagem, entre 0,5% e o teto da banca
        s = bank * min(cap, max(0.005, 0.25 * edge / (odd - 1))) * (0.5 if kind == "half" else 1)
    elif ativo: s = min(BASE * mult * (0.5 if kind == "half" else 1), bank * cap)
    else: s = bank * TESTE * (0.5 if kind == "half" else 1)
    return round(max(0.0, min(s, livre)), 2)

def fill(now_iso):
    st = load()
    now = datetime.datetime.fromisoformat(now_iso)
    have = {p["id"] for p in st["picks"]}
    open_games = [g for g in st["games"].values() if datetime.datetime.fromisoformat(g["kickoff"]) > now]
    open_games.sort(key=lambda g: g["kickoff"])
    for g in open_games:
        if sport_of(g) == "futebol": features(g)
    added = 0
    for b in B:
        st_kind_stake = None
        if b["id"] not in CONTROLES and falido(st, b["id"]): continue
        for g in open_games:
            if sport_of(g) != b.get("sport", "futebol"): continue
            pid = f"{b['id']}|{g['id']}"
            if pid in have: continue
            r = random.Random(pid)
            spec = b["rule"](g, r)
            if not spec: continue
            if st_kind_stake is None: st_kind_stake = stake_for(st, b)
            prob, odd = price(g, spec); mk, sel = label(g, spec)
            mult, z, ativo = decide(st, b["id"], g["league"], odd)
            stake = stake_of(st, b, mult, ativo, odd=odd, edge=seg_edge(st, b["id"], g["league"], odd)[0])
            st["picks"].append({"id": pid, "bettor": b["id"], "game": g["id"], "day": g["day"], "kickoff": g["kickoff"],
                                "spec": spec, "market": mk, "sel": sel, "prob": prob, "odd": odd, "odd_inicial": odd,
                                "stake": stake, "estudo": not ativo, "z": round(z, 2) if z is not None else None,
                                "grade": None, "pts": None})
            have.add(pid); added += 1
    for f in st.get("fusions", []):
        if f.get("tipo") in ("conselho", "revisao") or f.get("aposentado") or falido(st, f["id"]): continue
        for g in open_games:
            if sport_of(g) != "futebol": continue
            pa, pb = f"{f['id']}|{g['id']}|a", f"{f['id']}|{g['id']}|b"
            if pa in have: continue
            sa = BY[f["a"]]["rule"](g, random.Random(f"{f['a']}|{g['id']}"))
            sb = BY[f["b"]]["rule"](g, random.Random(f"{f['b']}|{g['id']}"))
            if not sa or not sb: continue
            mult, z, ativo = decide(st, f["id"], g["league"], price(g, sa)[1])
            for pid, spec in ((pa, sa), (pb, sb)):
                stake = stake_of(st, f, mult, ativo, cap=0.025)
                prob, odd = price(g, spec); mk, sel = label(g, spec)
                st["picks"].append({"id": pid, "bettor": f["id"], "game": g["id"], "day": g["day"], "kickoff": g["kickoff"],
                                    "spec": spec, "market": mk, "sel": sel, "prob": prob, "odd": odd, "odd_inicial": odd, "stake": stake,
                                    "estudo": not ativo, "grade": None, "pts": None})
                have.add(pid); added += 1
    for day in sorted({g["day"] for g in open_games}):
        dg = [g for g in open_games if g["day"] == day and sport_of(g) == "futebol"]
        for acc in ACCAS:
            pid = f"{acc['id']}|{day}"
            if pid in have or falido(st, acc["id"]): continue
            if acc["id"] == "jack":
                legs = [(g, S("1x2", sel=fav(g))) for g in dg if pf(g) >= .70]
            elif acc["id"] == "barbossa":
                legs = sorted(((g, S("ou", line=1.5, over=True)) for g in dg), key=lambda x: -price(*x)[0])[:4]
            else:
                legs = sorted(((g, S("btts", yes=True)) for g in dg), key=lambda x: -price(*x)[0])[:3]
            if len(legs) < 2: continue
            p = 1; odd = 1
            for g, s in legs: pr, od = price(g, s); p *= pr; odd *= od
            sel = " + ".join(f"{label(g, s)[1]} ({g['home']}-{g['away']})" for g, s in legs)
            mult, z, ativo = decide(st, acc["id"], "multi", min(odd, 500))
            stake = stake_of(st, acc, mult, ativo)
            st["picks"].append({"id": pid, "bettor": acc["id"], "game": None, "day": day, "kickoff": min(g["kickoff"] for g, _ in legs),
                                "legs": [{"game": g["id"], "spec": s} for g, s in legs], "market": f"Múltipla ({len(legs)} jogos)",
                                "sel": sel, "prob": round(p, 4), "odd": round(min(odd, 500), 2), "odd_inicial": round(min(odd, 500), 2), "stake": stake, "estudo": not ativo, "grade": None, "pts": None})
            have.add(pid); added += 1
    added += fill_conselhos(st, open_games, have)
    added += fill_revisoes(st, open_games, have)
    save(st); print(added, "palpites novos;", len(st["picks"]), "no total")

def settle(path):
    st = load()
    for gid, r in json.load(open(path)).items():
        if gid in st["games"]:
            st["games"][gid]["result"] = r; st["games"][gid]["status"] = r.get("status", "final")
    need = {"corners": "ch", "cards": "cards", "goals": "hh", "shots": "stats", "mins": "g1"}
    for p in st["picks"]:
        if p["grade"] is not None: continue
        p.setdefault("stake", BASE)
        if p.get("legs"):
            out = []
            for l in p["legs"]:
                g = st["games"][l["game"]]
                if g["status"] != "final": out = None; break
                out.append(grade(l["spec"], g["result"]))
            if out is None: continue
            p["grade"] = 1 if all(x == 1 for x in out) else -1
        else:
            g = st["games"][p["game"]]
            if g["status"] != "final": continue
            fam = FAMILY.get(p["spec"]["t"], "goals"); r = g["result"]
            if fam == "mins" and (r.get("fh") or 0) + (r.get("fa") or 0) == 0:
                p["grade"] = grade(p["spec"], r)
            elif fam == "shots" and not ((r.get("stats") or {}).get("home", {}).get("shots_total") is not None and (r.get("stats") or {}).get("away", {}).get("shots_total") is not None and (p["spec"]["t"] == "shots_ou" or (r["stats"]["home"].get("shots_on_target") is not None and r["stats"]["away"].get("shots_on_target") is not None))):
                p["grade"] = 0; p["void"] = True
            elif r.get(need[fam]) is None and fam != "goals":
                p["grade"] = 0; p["void"] = True
            else:
                if fam == "goals" and r.get("hh") is None: r = dict(r, hh=0, ha=0)
                p["grade"] = grade(p["spec"], r)
        gr = p["grade"]
        p["pts"] = round(p["stake"] * (p["odd"] - 1) * gr if gr > 0 else p["stake"] * gr, 2)
    save(st)

def settle_sql(path):
    """Apura a partir de liga_resultados_json() do Supabase. Casa cada resultado com o jogo do estado
    (pelo id da ESPN; senão por horário e nomes dos times) e só mexe em jogos que ainda não estão finais."""
    st = load(); rows = json.load(open(path)); out = {}; sem = []
    by_espn = {}
    for g in st["games"].values():
        if g.get("status") == "final": continue
        e = espn_of(g)
        if e: by_espn[str(e)] = g["id"]
    abertos = [g for g in st["games"].values() if g.get("status") != "final"]
    for r in rows:
        gid = by_espn.get(str(r.get("id")))
        if not gid:
            k = datetime.datetime.fromisoformat(str(r["kickoff"]).replace(" ", "T"))
            if k.tzinfo is None: k = k.replace(tzinfo=datetime.timezone.utc)
            best = None
            for g in abertos:
                gk = datetime.datetime.fromisoformat(g["kickoff"])
                if abs((gk - k).total_seconds()) > 1500: continue
                s1 = difflib.SequenceMatcher(None, _norm(g["home_name"]), _norm(r["home"])).ratio()
                s2 = difflib.SequenceMatcher(None, _norm(g["away_name"]), _norm(r["away"])).ratio()
                if s1 < 0.6 or s2 < 0.6: continue
                if best is None or s1 + s2 > best[0]: best = (s1 + s2, g["id"])
            gid = best[1] if best else None
        if not gid:
            sem.append(f'{r.get("home")} x {r.get("away")}'); continue
        if gid in out: continue
        res = {"status": "final", "fh": r.get("fh"), "fa": r.get("fa")}
        for k2 in ("hh", "ha", "ch", "ca", "cards", "g1", "stats"):
            if r.get(k2) is not None: res[k2] = r[k2]
        if r.get("fonte") == "espn": res["fonte"] = "ESPN via Supabase"; res["espn_id"] = r.get("id")
        out[gid] = res
    tmp = os.path.join(HERE, "results_sql.json"); json.dump(out, open(tmp, "w"), ensure_ascii=False)
    print(len(out), "jogos casados de", len(rows), "resultados;", len(sem), "sem casamento:", sem[:5])
    if out: settle(tmp)

def stats_of(st, bid):
    done = [p for p in st["picks"] if p["bettor"] == bid and p.get("grade") is not None and not p.get("void") and p.get("stake", 0) > 0]
    staked = sum(p["stake"] for p in done); lucro = sum(p["pts"] for p in done)
    return len(done), lucro, (lucro / staked if staked else 0)

def fuse(a, b, nome=None):
    st = load(); st.setdefault("fusions", [])
    f = fusion_entry(a, b, nome)
    if any(x["id"] == f["id"] for x in st["fusions"]): print("já existe"); return
    st["fusions"].append(f); save(st); print("fusão criada:", f["nome"])

def auto_fuse(min_n=15, per_run=2, max_total=20):
    """Funde os melhores apostadores (lucro e ROI positivos, amostra mínima) de mercados diferentes."""
    st = load(); st.setdefault("fusions", [])
    ok = []
    for b in B:
        n, lucro, roi = stats_of(st, b["id"])
        if n >= min_n and lucro > 0 and b["mercado"] not in ("Controle aleatório", "Gestão de banca"):
            ok.append((roi, b))
    ok.sort(key=lambda x: -x[0]); top = [b for _, b in ok[:8]]
    made = 0; ids = {f["id"] for f in st["fusions"]}
    for i, x in enumerate(top):
        for y in top[i + 1:]:
            if made >= per_run or len(st["fusions"]) >= max_total: break
            if x["mercado"] == y["mercado"]: continue
            f = fusion_entry(x["id"], y["id"])
            if f["id"] in ids or f"fus_{y['id']}_{x['id']}" in ids: continue
            st["fusions"].append(f); ids.add(f["id"]); made += 1
            print("fusão criada:", f["nome"], "=", x["nome"], "+", y["nome"])
    save(st)
    if not made: print("nenhuma fusão nova (amostra insuficiente ou já existentes)")

def reprice(now_iso):
    """Recalcula a odd de palpites pendentes de jogos que ainda não começaram (após calibração ou odds novas)."""
    st = load(); now = datetime.datetime.fromisoformat(now_iso); n = 0
    for g in st["games"].values():
        g["odds"] = odds_board(g)
    FUS = {f["id"] for f in st.get("fusions", [])}
    global BYALL
    BYALL = {x["id"]: x for x in [*B, *ACCAS, *st.get("fusions", [])]}
    for p in st["picks"]:
        if p["grade"] is not None: continue
        if p.get("legs") or p["bettor"] in FUS:   # múltiplas e fusões: só reaplica a decisão de apostar ou estudar
            if datetime.datetime.fromisoformat(p["kickoff"]) <= now: continue
            lgk = "multi" if p.get("legs") else st["games"][p["game"]]["league"]
            mult, z, ativo = decide(st, p["bettor"], lgk, p["odd"])
            cap = 0.05 if p.get("legs") else 0.025
            ent = BYALL.get(p["bettor"]) or {"id": p["bettor"]}
            p["stake"] = 0.0
            p["stake"] = stake_of(st, ent, mult, ativo, cap=cap)
            p["estudo"] = not ativo
            continue
        if not p.get("game"): continue
        g = st["games"][p["game"]]
        if datetime.datetime.fromisoformat(g["kickoff"]) <= now: continue
        prob, odd = price(g, p["spec"])
        p["prob"], p["odd"] = prob, odd
        b = BY.get(p["bettor"])
        if b:
            mult, z, ativo = decide(st, b["id"], g["league"], odd)
            p["stake"] = 0.0
            p["stake"] = stake_of(st, b, mult, ativo)
            p["estudo"] = not ativo; p["z"] = round(z, 2) if z is not None else None
        p["fonte"] = "DraftKings" if real_odd(g, p["spec"]) else ("calibrada" if calib_prob(g, p["spec"]) is not None else None)
        n += 1
    save(st); print(n, "palpites repreçados")

def restake(now_iso):
    """Uma vez: dá dinheiro aos palpites de estudo já jogados (2% da banca da época), em ordem cronológica."""
    st = load(); now = datetime.datetime.fromisoformat(now_iso)
    FUS = {f["id"] for f in st.get("fusions", [])}
    por = {}
    for p in st["picks"]: por.setdefault(p["bettor"], []).append(p)
    mudou = 0
    for bid, ps in por.items():
        ps.sort(key=lambda p: (p.get("kickoff") or p["day"], p["id"]))
        bank = BANCA
        for p in ps:
            ko = datetime.datetime.fromisoformat(p["kickoff"]) if p.get("kickoff") else None
            if ko and ko > now: continue
            if p.get("stake", 0) == 0 and bid not in CONTROLES:
                k = (BY.get(bid) or {}).get("stake", "flat")
                v = {"flat1": 10, "flat5": 50, "pct10": bank * .10, "martingale": BASE, "fibonacci": BASE}.get(k, bank * TESTE * (0.5 if bid in FUS else 1))
                p["stake"] = round(min(v, bank), 2) if bank >= FALENCIA else 0.0
                mudou += 1
            if p.get("grade") is not None:
                gr = p["grade"]
                p["pts"] = round(p["stake"] * (p["odd"] - 1) * gr if gr > 0 else p["stake"] * gr, 2)
                bank += p["pts"]
    save(st); print(mudou, "palpites de estudo agora valem dinheiro")

def migrate():
    """Converte palpites da v1 (sem stake/kickoff) e recalcula a grade de odds dos jogos."""
    st = load()
    for g in st["games"].values():
        g["odds"] = odds_board(g)
    for p in st["picks"]:
        p.setdefault("stake", BASE)
        if p.get("game"): p.setdefault("kickoff", st["games"][p["game"]]["kickoff"])
    save(st)

def espn_of(g):
    return (g.get("real") or {}).get("espn_id") or (g.get("result") or {}).get("espn_id") or g.get("espn_id")

def close_of(p, g, c):
    """Odd de fechamento do mesmo lado do palpite (só 1X2 e total de gols na linha principal)."""
    sel = str(p.get("sel", "")); m = p.get("market", "")
    if m == "Resultado":
        if sel == "Empate": return c.get("d")
        if sel.endswith(" vence"):
            nome = sel[:-6]
            if nome == g.get("home_name"): return c.get("h")
            if nome == g.get("away_name"): return c.get("a")
        return None
    if m == "Total de gols":
        mm = re.match(r"(Mais|Menos) de ([0-9.]+)$", sel)
        if not mm or c.get("l") is None or abs(float(mm.group(2)) - c["l"]) > 1e-9: return None
        return c.get("o") if mm.group(1) == "Mais" else c.get("u")
    return None

def clv(path):
    """Mede o CLV: odd escolhida / odd de fechamento - 1. Positivo = pegou preço melhor que o mercado no fim."""
    st = load(); C = json.load(open(path)); n = 0
    for p in st["picks"]:
        g = st["games"].get(p.get("game"))
        if not g or not p.get("odd_inicial"): continue  # só palpites com odd gravada na hora da escolha
        e = espn_of(g)
        c = C.get(str(e)) if e else None
        if not c: continue
        co = close_of(p, g, c)
        if not co: continue
        p["close"] = co; p["clv"] = round(p["odd_inicial"] / co - 1, 4); n += 1
    save(st); print(n, "palpites com CLV medido")

def _hist(h):
    """O resumo do backtest pode vir como lista [n, roi, banca, teste sem aprender, teste aprendendo, n do teste]
    ou como objeto do Supabase (n, roi, banca, val_sem, val_com, val_n). A página sempre recebe a lista."""
    if isinstance(h, dict):
        return [h.get("n"), h.get("roi"), h.get("banca"), h.get("val_sem"), h.get("val_com"), h.get("val_n")]
    return h

# ---------- novos (21/11): mercados que a Stake oferece e ainda não testávamos ----------
EC = "Escanteios por time"
bet("jinx", "Jinx", "Arcane", "Azarão sem pressão", EC, "Azarão cobra menos de 4,5 escanteios quando o favorito tem 60% ou mais de chance.", lambda g, r: S("team_corners", sel=dog(g), line=4.5, over=False) if pf(g) >= .60 else None)
bet("vi", "Vi", "Arcane", "Favorito domina escanteios", EC, "Favorito cobra mais de 4,5 escanteios quando tem 55% ou mais de chance.", lambda g, r: S("team_corners", sel=fav(g), line=4.5, over=True) if pf(g) >= .55 else None)
bet("ekko", "Ekko", "Arcane", "Jogo travado nos escanteios", EC, "Mandante cobra menos de 5,5 escanteios em jogo equilibrado (ninguém passa de 45%).", lambda g, r: S("team_corners", sel="home", line=5.5, over=False) if pf(g) < .45 else None)
bet("eleven", "Eleven", "Stranger Things", "Favorito vence um tempo", "Tempos", "Favorito vence pelo menos um dos tempos, com 55% ou mais de chance de vencer.", lambda g, r: S("win_half", sel=fav(g), yes=True) if pf(g) >= .55 else None)
bet("hopper", "Hopper", "Stranger Things", "Massacre", "Tempos", "Favorito vence os dois tempos, com 70% ou mais de chance de vencer.", lambda g, r: S("win_both", sel=fav(g), yes=True) if pf(g) >= .70 else None)
bet("wednesday", "Wandinha", "Wandinha", "2º tempo aberto", "Gols", "Mais de 1,5 gols no 2º tempo quando o modelo espera 3 gols ou mais no jogo.", lambda g, r: S("sh_ou", line=1.5, over=True) if g["model"]["lh"] + g["model"]["la"] >= 3 else None)
bet("enid", "Enid", "Wandinha", "2º tempo morno", "Gols", "Menos de 1,5 gols no 2º tempo quando o modelo espera menos de 2,3 gols.", lambda g, r: S("sh_ou", line=1.5, over=False) if g["model"]["lh"] + g["model"]["la"] < 2.3 else None)
bet("dustin", "Dustin", "Stranger Things", "Favorito vence com gols dos dois", "Gols", "Favorito vence e ambas marcam, quando os dois times devem marcar (o mais fraco espera 1,1 gol ou mais) e o favorito tem 55% ou mais.", lambda g, r: S("res_btts", sel=fav(g), yes=True) if min(g["model"]["lh"], g["model"]["la"]) >= 1.1 and pf(g) >= .55 else None)

# ---------- laboratório: avalia, aposenta, funde e cria personagens novos sozinho a cada rodada ----------
NOMES_NOVOS = ["Professor X", "Nick Fury", "Mestre Splinter", "Optimus Prime", "Mufasa", "Rafiki", "Senhor Miyagi", "Mestre Shifu",
               "Geralt de Rívia", "Yennefer", "Ciri", "Kratos", "Lara Croft", "Cloud Strife", "Tifa", "Edward Elric", "Alphonse Elric",
               "Roy Mustang", "Senku", "Kurisu Makise", "Okabe Rintaro", "Anya Forger", "Loid Forger", "Yor Forger", "Frieren", "Fern",
               "Himmel", "Rimuru", "Gintoki", "Vash", "Major Kusanagi", "Chrollo", "Ezio Auditore", "Mestre Chefe", "Atreus", "Sephiroth"]
COMPLETO_N = 60   # apostas ao vivo para julgar um personagem do laboratório

def _unit(p): return (p["odd"] - 1) * p["grade"] if p["grade"] > 0 else p["grade"]

def nota_justa(st, bid, before=None, _cache={}):
    """Lucro por aposta de mesmo tamanho, puxado para −3% até ter amostra (histórico de 5 temporadas + ao vivo). Igual à aba Top 10."""
    ps = [p for p in st["picks"] if p["bettor"] == bid and p["grade"] is not None and not p.get("void") and p.get("stake", 0) > 0
          and (before is None or (p.get("kickoff") or "") < before)]
    h = _hist(HISTORICO.get(bid)); H = (h[4] if h[4] is not None else h[3]) if h else 0
    hn = (h[5] or 0) if h and h[4] is not None else (round(h[0] * .35) if h else 0)
    return (sum(_unit(p) for p in ps) + H / 100 * hn - 0.03 * 60) / (len(ps) + hn + 60) * 100, len(ps)

def todos(st): return [*B, *ACCAS, *st.get("fusions", [])]

def ativo_lab(f): return not f.get("aposentado")

def top_pool(st, k, before=None):
    """Os k melhores pela nota justa, sem controles, sem preço de modelo puro e sem os conselhos (para não se copiarem)."""
    sc = []
    for b in todos(st):
        if b["id"] in CONTROLES or b["id"] in SEM_PRECO or b.get("tipo") == "conselho" or not ativo_lab(b): continue
        n, ln = nota_justa(st, b["id"], before)
        if ln >= 5 and n > 0: sc.append((n, b["id"]))
    return [i for _, i in sorted(sc, reverse=True)[:k]]

def direcao(spec):
    """Para onde a aposta aponta: linhas diferentes no mesmo sentido contam como a mesma ideia (menos de 3,5 e menos de 4,5 cartões)."""
    t = spec["t"]
    if t in ("1x2", "ml", "dnb", "dc", "ah", "win_nil"): return "resultado:" + spec["sel"]
    if t == "htft": return "resultado:" + spec["ft"]
    if t in ("ou", "ht_ou"): return "gols:" + ("mais" if spec["over"] else "menos")
    if t == "team_ou": return f"gols_{spec['sel']}:" + ("mais" if spec["over"] else "menos")
    if t == "cards_ou": return "cartoes:" + ("mais" if spec["over"] else "menos")
    if t == "corners_ou": return "escanteios:" + ("mais" if spec["over"] else "menos")
    if t in ("corners_1x2", "corners_ah"): return "escanteios_lado:" + spec["sel"]
    if t == "team_corners": return f"escanteios_{spec['sel']}:" + ("mais" if spec["over"] else "menos")
    if t == "sh_ou": return "gols:" + ("mais" if spec["over"] else "menos")
    if t in ("win_half", "win_both"): return "resultado:" + spec["sel"]
    if t == "res_btts": return "resultado:" + spec["sel"]
    if t in ("shots_ou", "sot_ou"): return "chutes:" + ("mais" if spec["over"] else "menos")
    return json.dumps(spec, sort_keys=True)

def _dirset(st, bid, _c={}):
    k = (id(st), bid)
    if k not in _c: _c[k] = {(p["game"], direcao(p["spec"])) for p in st["picks"] if p["bettor"] == bid and p.get("game") and p.get("spec")}
    return _c[k]

def _grupos(st, ids):
    """Quantas estratégias de fato independentes: quem escolhe a mesma aposta em mais de 60% das vezes conta como uma."""
    K = {i: _dirset(st, i) for i in ids}
    gs = []
    for i in ids:
        for g in gs:
            if any(min(len(K[i]), len(K[o])) and len(K[i] & K[o]) / min(len(K[i]), len(K[o])) > .6 for o in g): g.append(i); break
        else: gs.append([i])
    return len(gs)

def conselho_entry(nome, pool, k, linha, piso, pai=None):
    cid = f"cons_{pool}_{k}_{linha}_{str(piso).replace('.', '')}"
    lt = "a linha mais segura (maior chance)" if linha == "segura" else "a linha que paga mais"
    quem = f"um dos {pool} melhores (nota justa) aposta" if k == 1 else f"pelo menos {k} estratégias independentes entre os {pool} melhores (nota justa) apontam para o mesmo lado"
    return {"id": cid, "nome": nome, "tipo": "conselho", "pool": pool, "k": k, "linha": linha, "piso": piso, "pai": pai, "stake": "flat",
            "origem": "Laboratório · conselho" + (f" (mutação de {pai})" if pai else ""), "mercado": "Conselho", "estrategia": (f"Copia os {pool} melhores" if k == 1 else f"Consenso de {k}+ entre os {pool} melhores"),
            "regra": f"Só aposta quando {quem} no jogo. Se há mais de uma linha no mesmo sentido, escolhe {lt}; num jogo, fica com o lado que mais gente apoia" + (f", com odd mínima {piso:.2f}" if piso > 1 else "") + "."}

def evolve(now_iso):
    st = load(); F = st.setdefault("fusions", []); dia = now_iso[:10]; log = []
    # 1. aposenta quem já tem amostra e a nota justa ficou negativa (fusões e conselhos)
    for f in F:
        if not ativo_lab(f): continue
        n, ln = nota_justa(st, f["id"])
        if ln >= COMPLETO_N and n < -4.0:   # pior que apostar em tudo (margem da casa)
            f["aposentado"] = dia; log.append(f"aposentado: {f['nome']} (nota {n:+.1f}% em {ln} apostas)")
    ativos = [f for f in F if ativo_lab(f)]
    # 2. conselhos iniciais (uma vez)
    ids = {f["id"] for f in F}; usados = {b["nome"] for b in todos(st)}
    for nome, pool, k, linha, piso in [("Professor X", 10, 1, "segura", 1.0), ("Nick Fury", 10, 2, "segura", 1.0), ("Mestre Splinter", 5, 1, "segura", 1.0)]:
        e = conselho_entry(nome, pool, k, linha, piso)
        if e["id"] not in ids: F.append(e); ids.add(e["id"]); usados.add(nome); log.append(f"criado: {nome} — {e['regra']}")
    # 3. mutação: o melhor conselho com amostra gera um filho mudando um número (1 por rodada, no máximo 8 conselhos ativos)
    cons = [f for f in F if f.get("tipo") == "conselho" and ativo_lab(f)]
    julg = sorted(((nota_justa(st, f["id"]), f) for f in cons if nota_justa(st, f["id"])[1] >= 15), key=lambda x: -x[0][0])
    if julg and julg[0][0][0] > 0 and len(cons) < 8:
        pai = julg[0][1]; r = random.Random(dia + pai["id"])
        for _ in range(12):
            m = dict(pool=pai["pool"], k=pai["k"], linha=pai["linha"], piso=pai["piso"]); campo = r.choice(list(m))
            m[campo] = r.choice({"pool": [5, 10, 15, 20, 30], "k": [1, 2, 3], "linha": ["segura", "valor"], "piso": [1.0, 1.3, 1.5, 1.7, 2.0]}[campo])
            nome = next((x for x in NOMES_NOVOS if x not in usados), None)
            e = conselho_entry(nome or f"Conselho {len(cons) + 1}", m["pool"], m["k"], m["linha"], m["piso"], pai=pai["nome"])
            if e["id"] in ids or not nome: continue
            F.append(e); ids.add(e["id"]); usados.add(nome); log.append(f"mutação: {nome} nasce de {pai['nome']} — {e['regra']}"); break
    # 4. fusões: pais com nota justa positiva e mercados diferentes (máx. 2 por rodada, 20 ativas)
    pool = [i for i in top_pool(st, 8) if i in BY]
    fus_ativas = [f for f in F if f.get("tipo") not in ("conselho", "revisao") and ativo_lab(f)]; made = 0
    for i, a in enumerate(pool):
        for b in pool[i + 1:]:
            if made >= 2 or len(fus_ativas) >= 20: break
            if BY[a]["mercado"] == BY[b]["mercado"] or f"fus_{a}_{b}" in ids or f"fus_{b}_{a}" in ids: continue
            f = fusion_entry(a, b); F.append(f); ids.add(f["id"]); fus_ativas.append(f); made += 1
            log.append(f"fusão: {f['nome']} = {BY[a]['nome']} + {BY[b]['nome']}")
    log += revisoes(st, F, dia)
    st.setdefault("lab", []); st["lab"] = ([{"dia": dia, "msg": m} for m in log] + st["lab"])[:60]
    save(st); print("\n".join(log) or "laboratório: nada novo hoje")

def fill_conselhos(st, open_games, have):
    added = 0; pools = {}
    for f in st.get("fusions", []):
        if f.get("tipo") != "conselho" or not ativo_lab(f) or falido(st, f["id"]): continue
        if f["pool"] not in pools: pools[f["pool"]] = top_pool(st, f["pool"])
        P = set(pools[f["pool"]])
        for g in open_games:
            pid = f"{f['id']}|{g['id']}"
            if pid in have: continue
            D = collections.defaultdict(list)
            for p in st["picks"]:
                if p.get("game") == g["id"] and p["bettor"] in P and p.get("spec") and p.get("stake", 0) > 0: D[direcao(p["spec"])].append(p)
            best = None
            for d, ps in D.items():
                if _grupos(st, sorted({p["bettor"] for p in ps})) < f["k"]: continue
                opts = [(price(g, p["spec"]), p) for p in ps]
                opts = [x for x in opts if x[0][1] >= f["piso"]]
                if not opts: continue
                pick = max(opts, key=lambda x: x[0][0] if f["linha"] == "segura" else x[0][1])
                quem = len({p["bettor"] for p in ps})
                if best is None or quem > best[2]: best = (pick, d, quem)
            if not best: continue
            (prob, odd), p0 = best[0]
            mult, z, ativo = decide(st, f["id"], g["league"], odd); stake = stake_of(st, f, mult, ativo, odd=odd, edge=seg_edge(st, f["id"], g["league"], odd)[0])
            st["picks"].append({"id": pid, "bettor": f["id"], "game": g["id"], "day": g["day"], "kickoff": g["kickoff"], "spec": p0["spec"],
                                "market": p0["market"], "sel": p0["sel"], "prob": prob, "odd": odd, "odd_inicial": odd, "stake": stake,
                                "estudo": not ativo, "z": round(z, 2) if z is not None else None, "grade": None, "pts": None, "apoio": best[2]})
            have.add(pid); added += 1
    return added

# ---------- revisão: o personagem em crise entende que a estratégia não funciona e se corrige (nasce a versão 2, 3...) ----------
REV_BANCA = 600.0     # banca abaixo disso = crise
REV_NOTA = -5.0       # ou nota justa abaixo disso, com amostra
REV_MIN = 60          # só revisa com pelo menos tantas apostas já apuradas
REV_POR_RODADA = 3
REV_MAX_ATIVAS = 30
_FAIXAS = "abcde"; _LIM = {"a": (1.0, 1.5), "b": (1.5, 2.0), "c": (2.0, 3.0), "d": (3.0, 5.0), "e": (5.0, 999.0)}

def _sc(sub):
    """Lucro por unidade apostada, puxado para -3% (margem da casa) até ter amostra."""
    return (sum(_unit(p) for p in sub) - 0.03 * 60) / (len(sub) + 60)

def _passa(p, fl, pgames):
    if not (fl.get("odd_min", 0) <= p["odd"] < fl.get("odd_max", 999)): return False
    if fl.get("sem_ligas") and p["_lg"] in fl["sem_ligas"]: return False
    if fl.get("parceiro") and p["game"] not in pgames: return False
    return True

def diagnostico(st, bid, ja):
    """Onde ele perde? Procura (1) a faixa de odd onde a estratégia dele funciona, (2) ligas onde ele só perde e (3) um parceiro entre os melhores
    cuja concordância melhora o resultado. Só vale se o filtro também passa no último terço do histórico (que não foi usado para escolher)."""
    ps = [p for p in st["picks"] if p["bettor"] == bid and p.get("grade") is not None and not p.get("void") and p.get("spec") and p.get("game") in st["games"]]
    if len(ps) < REV_MIN: return None
    for p in ps: p["_lg"] = st["games"][p["game"]]["league"]
    ps.sort(key=lambda p: p.get("kickoff") or "")
    try:
        base = _sc(ps); fl = {}; cur = ps; why = []
        best = None
        for i in range(5):
            for j in range(i, 5):
                if i == 0 and j == 4: continue
                sub = [p for p in ps if i <= _FAIXAS.index(band_of(p["odd"])) <= j]
                if len(sub) >= 20 and (best is None or _sc(sub) > best[0]): best = (_sc(sub), i, j, sub)
        if best and best[0] > base + 0.02:
            fl["odd_min"], fl["odd_max"] = _LIM[_FAIXAS[best[1]]][0], _LIM[_FAIXAS[best[2]]][1]; cur = best[3]
            why.append(f"odd entre {fl['odd_min']:.2f} e {min(fl['odd_max'], 99):.2f}" if fl["odd_max"] < 999 else f"odd acima de {fl['odd_min']:.2f}")
        L = collections.defaultdict(list)
        for p in cur: L[p["_lg"]].append(p)
        ruins = [l for l, v in L.items() if len(v) >= 12 and sum(_unit(p) for p in v) / len(v) < -0.15]
        if ruins and len(cur) - sum(len(L[l]) for l in ruins) >= 25:
            fl["sem_ligas"] = sorted(ruins); cur = [p for p in cur if p["_lg"] not in ruins]
            why.append("fora de " + ", ".join(LG_NOME.get(l, l) for l in sorted(ruins)))
        pb = None
        for cand in top_pool(st, 8):
            if cand == bid or cand not in BY or BY[cand]["mercado"] == BY[bid]["mercado"] or BY[cand].get("sport", "futebol") != BY[bid].get("sport", "futebol"): continue
            gs = {p["game"] for p in st["picks"] if p["bettor"] == cand and p.get("spec")}
            sub = [p for p in cur if p["game"] in gs]
            if len(sub) >= 20 and (pb is None or _sc(sub) > pb[0]): pb = (_sc(sub), cand, gs, sub)
        pgames = set()
        if pb and pb[0] > _sc(cur) + 0.02:
            fl["parceiro"] = pb[1]; pgames = pb[2]; cur = pb[3]; why.append(f"só quando {BY[pb[1]]['nome']} também aposta")
        if not fl: return None
        # teste no último terço (fora da amostra usada para escolher)
        ho = ps[int(len(ps) * 0.67):]; hs = [p for p in ho if _passa(p, fl, pgames)]
        if len(hs) < 5 or sum(_unit(p) for p in hs) / len(hs) < sum(_unit(p) for p in ho) / len(ho): return None
        key = json.dumps(fl, sort_keys=True)
        if key in ja: return None
        return fl, why, len(cur), round(sum(_unit(p) for p in cur) / len(cur) * 100, 1)
    finally:
        for p in ps: p.pop("_lg", None)

def revisao_entry(pai, v, fl, why):
    b = BY[pai]
    return {"id": f"rev_{pai}_v{v}", "nome": f"{b['nome']} v{v}", "tipo": "revisao", "pai": pai, "filtros": fl, "versao": v, "stake": "flat",
            "origem": f"Revisão de {b['nome']} (v{v})", "mercado": "Revisão", "estrategia": f"{b['estrategia']} · corrigida",
            "regra": f"{b['regra']} Revisão: " + "; ".join(why) + "."}

def revisoes(st, F, dia):
    """Quem está em crise (banca abaixo de R$ 600 ou nota justa ruim) estuda onde erra, aprende com os melhores e nasce de novo, melhorado. A versão antiga segue jogando."""
    log = []; feitos = 0
    if len([f for f in F if f.get("tipo") == "revisao" and ativo_lab(f)]) >= REV_MAX_ATIVAS: return log
    cands = []
    for b in B:
        bid = b["id"]
        if bid in CONTROLES or bid in SEM_PRECO or b.get("sport", "futebol") not in ("futebol", "nba", "nhl", "nfl"): continue
        cadeia = [f for f in F if f.get("tipo") == "revisao" and f["pai"] == bid]
        if any(ativo_lab(f) for f in cadeia): continue
        bank = bankroll(st, bid); n, ln = nota_justa(st, bid)
        if ln >= REV_MIN and (bank < REV_BANCA or n < REV_NOTA): cands.append((n, bid, len(cadeia)))
    for n, bid, nc in sorted(cands):
        if feitos >= REV_POR_RODADA: break
        ja = {json.dumps(f["filtros"], sort_keys=True) for f in F if f.get("tipo") == "revisao" and f["pai"] == bid}
        d = diagnostico(st, bid, ja)
        if not d: continue
        fl, why, nn, roi = d
        e = revisao_entry(bid, nc + 2, fl, why)
        if any(x["id"] == e["id"] for x in F): continue
        F.append(e); feitos += 1
        log.append(f"revisão: {BY[bid]['nome']} em crise (nota {n:+.1f}%) vira {e['nome']} — " + "; ".join(why) + f". No passado essa regra teria dado {roi:+.1f}% por aposta em {nn} apostas; agora treina com 2% da banca.")
    return log

def fill_revisoes(st, open_games, have):
    added = 0
    for f in st.get("fusions", []):
        if f.get("tipo") != "revisao" or not ativo_lab(f) or falido(st, f["id"]) or f["pai"] not in BY: continue
        b = BY[f["pai"]]; fl = f["filtros"]
        for g in open_games:
            if sport_of(g) != b.get("sport", "futebol"): continue
            pid = f"{f['id']}|{g['id']}"
            if pid in have: continue
            spec = b["rule"](g, random.Random(f"{b['id']}|{g['id']}"))
            if not spec: continue
            prob, odd = price(g, spec)
            if not (fl.get("odd_min", 0) <= odd < fl.get("odd_max", 999)) or g["league"] in fl.get("sem_ligas", []): continue
            if fl.get("parceiro") and fl["parceiro"] in BY and not BY[fl["parceiro"]]["rule"](g, random.Random(f"{fl['parceiro']}|{g['id']}")): continue
            mk, sel = label(g, spec)
            mult, z, ativo = decide(st, f["id"], g["league"], odd)
            stake = stake_of(st, f, mult, ativo, odd=odd, edge=seg_edge(st, f["id"], g["league"], odd)[0])
            st["picks"].append({"id": pid, "bettor": f["id"], "game": g["id"], "day": g["day"], "kickoff": g["kickoff"], "spec": spec, "market": mk, "sel": sel,
                                "prob": prob, "odd": odd, "odd_inicial": odd, "stake": stake, "estudo": not ativo, "z": round(z, 2) if z is not None else None, "grade": None, "pts": None})
            have.add(pid); added += 1
    return added

def insight_track(st):
    """Quanto os Insights teriam rendido: a cada dia, o Top 10 pela nota justa usando só o que se sabia até a véspera."""
    unit = lambda p: (p["odd"] - 1) * p["grade"] if p["grade"] > 0 else p["grade"]
    done = collections.defaultdict(list)
    for p in st["picks"]:
        if p["grade"] is not None and not p.get("void") and p.get("stake", 0) > 0: done[p["bettor"]].append(p)
    ids = [b["id"] for b in [*B, *ACCAS, *st.get("fusions", [])] if b["id"] not in CONTROLES and b["id"] not in SEM_PRECO]
    def nota(bid, d):
        ps = [p for p in done[bid] if (p.get("kickoff") or "") < d]
        if len(ps) < 5: return None
        h = _hist(HISTORICO.get(bid)); H = (h[4] if h[4] is not None else h[3]) if h else 0
        hn = (h[5] or 0) if h and h[4] is not None else (round(h[0] * .35) if h else 0)
        return (sum(unit(p) for p in ps) + H / 100 * hn - 0.03 * 60) / (len(ps) + hn + 60) * 100
    R = {1: [0, 0.0], 2: [0, 0.0], 3: [0, 0.0]}; dias = 0
    for d in sorted({p["day"] for ps in done.values() for p in ps})[1:]:
        sc = sorted(((n, b) for b in ids if (n := nota(b, d)) is not None), reverse=True)[:10]
        if not sc: continue
        dias += 1; M = collections.defaultdict(list)
        for _, b in sc:
            for p in done[b]:
                if p["day"] == d: M[(p.get("game") or p["id"], direcao(p["spec"]) if p.get("spec") else p["market"] + p["sel"])].append(p)
        for v in M.values():
            pk = max(v, key=lambda p: p.get("prob") or 0)   # mesma direção: fica com a linha mais segura
            f = min(3, len({p["bettor"] for p in v})); R[f][0] += 1; R[f][1] += unit(pk)
            k = "confiante" if any(not p.get("estudo") for p in v) else "teste"; R.setdefault(k, [0, 0.0]); R[k][0] += 1; R[k][1] += unit(pk)
    out = {str(f): {"n": n, "roi": round(u / n * 100, 1) if n else None} for f, (n, u) in R.items()}
    tn = sum(R[f][0] for f in (1, 2, 3)); tu = sum(R[f][1] for f in (1, 2, 3))
    out["todas"] = {"n": tn, "roi": round(tu / tn * 100, 1) if tn else None}; out["dias"] = dias
    return out

def hist_seg(bid, league, odd, _H={}):
    """Só o teste de 5 temporadas (sem resultados ao vivo): (vantagem, amostra, z). Base honesta para o sinal verde."""
    if not _H:
        for b_, rows in POLICY.items():
            for lg, bd, n, stk, lu in rows:
                if stk: _H[(b_, lg, bd)] = ((lu - 0.03 * BASE * 60) / (stk + BASE * 60), n, (lu / stk) / (SDU[bd] / math.sqrt(max(1, n))))
    return _H.get((bid, league, band_of(odd)))

def verde(bid, league, odd):
    h = hist_seg(bid, league, odd)
    return bool(h and h[0] >= 0.03 and h[1] >= 40 and h[2] >= 1.0 and bid not in SEM_PRECO and bid not in CONTROLES)

def verde_track(st):
    """Resultado ao vivo de quem seguiu só as apostas verdes (uma por jogo e direção, a linha mais segura)."""
    M = {}
    for p in st["picks"]:
        if p["grade"] is None or p.get("void") or not p.get("game") or not p.get("spec") or p.get("stake", 0) <= 0: continue
        if not verde(p["bettor"], st["games"][p["game"]]["league"], p["odd"]): continue
        k = (p["game"], direcao(p["spec"]))
        if k not in M or (p.get("prob") or 0) > (M[k].get("prob") or 0): M[k] = p
    v = list(M.values()); u = sum(_unit(p) for p in v)
    dias = collections.defaultdict(float)
    for p in v: dias[p["day"]] += _unit(p)
    return {"n": len(v), "roi": round(u / len(v) * 100, 1) if v else None, "acertos": sum(1 for p in v if p["grade"] > 0),
            "dias": len(dias), "dias_pos": sum(1 for x in dias.values() if x > 0)}

def anexa_vantagem(st, picks):
    for p in picks:
        if p.get("grade") is None and p.get("game") and p.get("stake", 0) > 0:
            e, n, at = seg_edge(st, p["bettor"], st["games"][p["game"]]["league"], p["odd"])
            if e is not None: p["edge"] = round(e * 100, 1); p["edge_n"] = n
            h = hist_seg(p["bettor"], st["games"][p["game"]]["league"], p["odd"])
            if h: p["hedge"] = round(h[0] * 100, 1); p["hn"] = h[1]; p["hz"] = round(h[2], 2)
            if verde(p["bettor"], st["games"][p["game"]]["league"], p["odd"]): p["verde"] = 1

def render():
    st = load()
    board = []
    for b in [*B, *ACCAS, *st.get("fusions", [])]:
        ps = [p for p in st["picks"] if p["bettor"] == b["id"]]
        done = [p for p in ps if p["grade"] is not None and not p.get("void") and p.get("stake", 0) > 0]
        wins = sum(1 for p in done if p["grade"] > 0); losses = sum(1 for p in done if p["grade"] < 0)
        lucro = round(sum(p["pts"] for p in done), 2); staked = sum(p["stake"] for p in done)
        est = [p for p in ps if p.get("estudo")]
        est_done = [p for p in est if p["grade"] is not None and not p.get("void")]
        est_lu = sum(BASE * (p["odd"] - 1) * p["grade"] if p["grade"] > 0 else BASE * p["grade"] for p in est_done)
        T = seg_table(st, b["id"]) if b["id"] not in CONTROLES else {}
        ativos = [f"{LG_NOME.get(l, l)} · {BANDS[bb]}" for (l, bb), x in sorted(T.items(), key=lambda kx: -kx[1]["z"]) if x["ativo"]]
        board.append({"status": "controle" if b["id"] in CONTROLES else ("aposentado" if b.get("aposentado") else "falido" if BANCA + lucro < FALENCIA else ("apostando" if ativos else "estudando")),
                      "ativos": ativos[:6], "diario": diario(st, b["id"]), "hist": _hist(HISTORICO.get(b["id"])), "sem_preco": b["id"] in SEM_PRECO,
                      "estudo_n": len(est_done), "estudo_lucro": round(est_lu, 2),
                      "id": b["id"], "nome": b["nome"], "origem": b["origem"], "estrategia": b["estrategia"],
                      "mercado": b["mercado"], "regra": b["regra"], "gestao": b.get("stake", "flat"),
                      "apostas": sum(1 for p in ps if p.get("stake", 0) > 0), "apuradas": len(done), "acertos": wins, "erros": losses,
                      "pendentes": sum(1 for p in ps if p["grade"] is None and p.get("stake", 0) > 0), "lucro": lucro,
                      "banca": round(BANCA + lucro, 2), "emjogo": round(sum(p["stake"] for p in ps if p["grade"] is None), 2),
                      "roi": round(lucro / staked * 100, 1) if staked else None,
                      "taxa": round(wins / (wins + losses) * 100) if wins + losses else None,
                      "clv_n": len([p for p in ps if p.get("clv") is not None]),
                      "clv": round(sum(p["clv"] for p in ps if p.get("clv") is not None) / max(1, len([p for p in ps if p.get("clv") is not None])) * 100, 2)})
    anexa_vantagem(st, st["picks"])
    data = {"board": board, "games": list(st["games"].values()), "picks": st["picks"], "days": st["days"], "banca": BANCA, "track": insight_track(st), "verde": verde_track(st), "lab": st.get("lab", []),
            "gerado_em": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
    tpl = open(os.path.join(HERE, "template.html")).read()
    open(os.path.join(HERE, "index.html"), "w").write(tpl.replace("/*DATA*/null", json.dumps(data, ensure_ascii=False, separators=(",", ":"))))
    print("index.html gerado,", len(board), "apostadores")

if __name__ == "__main__":
    c = sys.argv[1]
    if c == "add-games": add_games(sys.argv[2])
    elif c == "fill": fill(sys.argv[2] if len(sys.argv) > 2 else datetime.datetime.now(datetime.timezone.utc).isoformat())
    elif c == "settle": settle(sys.argv[2])
    elif c == "migrate": migrate()
    elif c == "attach-odds": attach_odds(sys.argv[2])
    elif c == "clv": clv(sys.argv[2])
    elif c == "settle-sql": settle_sql(sys.argv[2])
    elif c == "reprice": reprice(sys.argv[2] if len(sys.argv) > 2 else datetime.datetime.now(datetime.timezone.utc).isoformat())
    elif c == "render": render()
    elif c == "restake": restake(sys.argv[2] if len(sys.argv) > 2 else datetime.datetime.now(datetime.timezone.utc).isoformat())
    elif c == "fuse": fuse(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None)
    elif c == "auto-fuse": auto_fuse()
    elif c == "evolve": evolve(sys.argv[2] if len(sys.argv) > 2 else datetime.datetime.now(datetime.timezone.utc).isoformat())
