import json, os, subprocess, sys, datetime, urllib.request, shutil
from zoneinfo import ZoneInfo
HERE = os.path.dirname(os.path.abspath(__file__))
URL = "https://hfnlslnrxvzrrkqsvyxf.supabase.co/rest/v1/rpc/liga_feed"
KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImhmbmxzbG5yeHZ6cnJrcXN2eXhmIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTQwNzc2MDQsImV4cCI6MjEwOTY1MzYwNH0.4423zgkeE_zhcw-SUGNx42VLtPzn3d86chcJ4z-FOOI"
def sh(*a):
    r = subprocess.run([sys.executable, os.path.join(HERE, "engine.py"), *a], cwd=HERE, capture_output=True, text=True)
    print(f"$ engine {' '.join(a)}\n{r.stdout.strip()}")
    if r.returncode != 0:
        print(r.stderr); raise SystemExit(f"falhou: {' '.join(a)}")
def dump(n, o): json.dump(o, open(os.path.join(HERE, n), "w"), ensure_ascii=False)
def main():
    completo = True  # roda uma vez por dia (meia-noite): sempre a atualização completa
    req = urllib.request.Request(URL, data=json.dumps({"completo": completo}).encode(), method="POST",
        headers={"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    import time
    for tentativa in range(5):   # o Supabase às vezes devolve 500 por alguns segundos
        try:
            feed = json.load(urllib.request.urlopen(req, timeout=90)); break
        except Exception as e:
            if tentativa == 4: raise
            print("feed falhou, tentando de novo:", e); time.sleep(30 * (tentativa + 1))
    print("feed:", len(feed["jogos"]), "jogos,", len(feed["odds"]), "odds,", len(feed["fech"]), "fechamentos")
    if completo:
        for k, f in (("calib", "calib.json"), ("policy", "policy.json"), ("historico", "historico.json"), ("forma", "forma.json")):
            if feed.get(k): dump(f, feed[k])
    dump("resultados.json", feed["resultados"] or []); sh("settle-sql", "resultados.json")
    st = json.load(open(os.path.join(HERE, "state.json")))
    novos = [g for g in feed["jogos"] if g["id"] not in st["games"]]
    if novos: dump("jogos_novos.json", novos); sh("add-games", "jogos_novos.json")
    dump("supa_odds.json", feed["odds"]); sh("attach-odds", "supa_odds.json")
    dump("closing.json", feed["fech"]); sh("clv", "closing.json")
    if completo: sh("auto-fuse")
    sh("fill"); sh("reprice"); sh("render")
    os.makedirs(os.path.join(HERE, "docs"), exist_ok=True)
    shutil.copy(os.path.join(HERE, "index.html"), os.path.join(HERE, "docs", "index.html"))
    for f in ("resultados.json", "jogos_novos.json", "supa_odds.json", "results_sql.json", "index.html"):
        p = os.path.join(HERE, f)
        if os.path.exists(p): os.remove(p)
if __name__ == "__main__": main()
