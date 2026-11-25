"""Input for ops/intel/build_eval_v2.py: download the newest prod DB backup (app.services.db_backup, the backend settings' private bucket),
extract ONLY headline / summary / outlet names / timestamps / desk status of nd_events news rows and tt_topics, then delete the DB copy.
Prints no secrets. Run from backend/ with the production variables injected (read-only use of the backup bucket), e.g.

    npx @railway/cli run --service api -- python ../ops/intel/extract_news_backup.py OUT.json TMPDIR

OUT.json is local working data: do not commit it (eval_v2.json carries the text of the selected items)."""
import gzip, json, os, sqlite3, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))
from app.config.settings import get_settings
from app.services import db_backup
out = sys.argv[1]
s = get_settings(); info = db_backup.newest(s)
print("backup taken_at", info["taken_at"])
s3 = db_backup._client(s)
def names(src):
    try: src = json.loads(src) if isinstance(src, str) else (src or [])
    except Exception: src = []
    n, summ = [], ""
    for x in src if isinstance(src, list) else []:
        if not isinstance(x, dict): continue
        v = x.get("outlet") or x.get("name") or x.get("source") or ""
        if v and v not in n: n.append(v)
        if not summ and (x.get("summary") or "").strip(): summ = x["summary"].strip()[:600]
    return n[:8], summ
with tempfile.TemporaryDirectory(dir=sys.argv[2]) as tmp:
    gz = os.path.join(tmp, "b.db.gz"); dbf = os.path.join(tmp, "b.db")
    with open(gz, "wb") as f: s3.download_fileobj(s.portfolio_bucket, info["key"], f)
    with gzip.open(gz, "rb") as src, open(dbf, "wb") as dst:
        while (c := src.read(1 << 24)): dst.write(c)
    os.unlink(gz)
    con = sqlite3.connect(dbf); con.row_factory = sqlite3.Row
    res = {"taken_at": str(info["taken_at"]), "nd_events": [], "tt_topics": []}
    for r in con.execute("SELECT id,kind,parent_id,title,summary,category,status,sources,virality,first_seen,released_at,created_at FROM nd_events WHERE kind='news'"):
        o, _ = names(r["sources"])
        res["nd_events"].append({"id": r["id"], "kind": r["kind"], "parent_id": r["parent_id"], "title": r["title"], "summary": (r["summary"] or "")[:600],
            "category": r["category"], "status": r["status"], "outlets": o, "virality": r["virality"], "first_seen": str(r["first_seen"]),
            "released_at": str(r["released_at"]), "created_at": str(r["created_at"])})
    for r in con.execute("SELECT id,title,category,status,peak_score,sources,first_seen,last_seen,posted_at FROM tt_topics"):
        o, summ = names(r["sources"])
        res["tt_topics"].append({"id": r["id"], "title": r["title"], "summary": summ, "category": r["category"], "status": r["status"],
            "peak_score": r["peak_score"], "outlets": o, "first_seen": str(r["first_seen"]), "last_seen": str(r["last_seen"]), "posted_at": str(r["posted_at"])})
    con.close()
json.dump(res, open(out, "w", encoding="utf-8"), default=str, ensure_ascii=False)
print({k: len(v) for k, v in res.items() if isinstance(v, list)})
