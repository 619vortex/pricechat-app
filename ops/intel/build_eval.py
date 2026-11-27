"""Build the Phase 0 eval set (ops/intel/eval_v1.json) from the replay fixture + a backup extract (news/trending rows only).
Labels: tier M(major) S(significant) N(notable) X(noise), Claude-labelled 2026-11-25 with the desk's importance tiers;
the fixture's editor label (publish/covered/went_out/stay_out) is kept beside it for cross-checking. Owner to review."""
import json, re, sys

FIX, EXTRACT, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
FIXTURE = {  # fixture news items -> tier (None = excluded: live page / ambiguous)
 556:"X",557:"X",558:"S",559:"S",578:"M",579:"N",590:"X",592:"S",615:"N",639:"X",645:"M",647:"S",648:"S",668:"X",671:"N",674:"M",
 686:"S",687:"N",688:"M",695:"S",735:"S",747:"X",760:"X",762:"S",771:"S",780:"N",781:"S",790:"M",791:"S",798:"X",801:"N",830:"M",
 866:"M",966:"S",969:"M",993:"S",1002:"M",1006:"S",1010:"S",1014:"S",1027:"S",1053:"N",1068:"X",1079:"S",1092:"S",1093:"X",1098:"S",
 1105:"S",1118:"S",1130:"X",1134:"S",1135:"S",1137:"S",1139:"X",1140:"S",1143:"S",1144:"X",1149:"S",1152:"S",1145:"S",1162:"M",1173:"M",
 "trend-45827":"S","trend-trump-calls":"X",
}
NEW = {  # backup rows -> tier
 "T45631":"S","T43369":"N","T45880":"N","T45684":"M","T45581":"S","T45221":"N","T43077":"X","T45668":"N","T44646":"N","T44565":"N",
 "T20887":"N","T39702":"N","T44312":"S","T39871":"N","T46104":"S","T45813":"S","T44602":"S","T46079":"X","T5103":"N","T43354":"N",
 "T44170":"X","T20513":"X","T22080":"N","T45304":"X","T43967":"S","T45129":"X","T45655":"X","T43238":"S","T44325":"S","T45824":"X",
 "T44241":"S","T45266":"N","T46331":"X","T44202":"N","T43182":"X","T43648":"X","T44297":"M","T45706":"N","T45746":"N","T45832":"X",
 "T43643":"X","T43269":"X","T42220":"X","T45557":"S","T43199":"S","T44102":"N","T44033":"X","T43242":"X","T44983":"X","T45764":"N",
 "T44635":"N","T45080":"X","T45306":"X","T45196":"N","T46219":"S","T45537":"X","T45609":"N","T45428":"X","T45497":"X","T43955":"X",
 "T43811":"N","T45988":"X","T46112":"X","T42857":"N","T44699":"N","T46452":"N","T46358":"N","T44255":"X","T46240":"S","T46037":"X",
 "T45328":"X","T45164":"N","T45365":"N","T46327":"X","T42938":"X","T44558":"M","T43559":"M","T43897":"N","T43097":"S","T43744":"N",
 "T45906":"X","T44179":"X","T46282":"N","T46151":"X","T43256":"X","T43670":"S","T44161":"M","T44286":"X","T44386":"S","T44472":"N",
 "T44507":"S","T44977":"X","T45193":"N","T45643":"N","T45751":"X","T46096":"S","T46401":"N","T44704":"S","T44403":"X","T45820":"X",
 "T44048":"X","T44222":"X","T45809":"N","T44520":"M","T45385":"N","T43695":"S","T46019":"X","T46083":"X","T43764":"N","T45444":"X",
 "T44214":"S","T45432":"X","T45743":"S","T45823":"X","T46051":"N","T44901":"X","T45741":"N","T44848":"N","T46486":"X",
 "N1175":"N","N1176":"S","N1177":"S","N1178":"X","N1179":"N","N1182":"S","N1184":"S","N1185":"S","N1186":"X","N1187":"N","N1190":"N",
 "N1191":"N","N1192":"N","N1193":"X","N1194":"S","N1196":"N","N1197":"N","N1199":"N","N1201":"N","N1207":"S","N1208":"N","N1209":"N",
 "N1210":"N","N1211":"M","N1212":"N","N1213":"N","N1214":"N","N1216":"N","N1218":"X","N1219":"N","N1225":"N","N1226":"X",
}
def outlets_of(sources):
    try: src = json.loads(sources) if isinstance(sources, str) else (sources or [])
    except Exception: src = []
    names = []
    for s in src if isinstance(src, list) else []:
        n = (s.get("name") or s.get("source") or s.get("outlet") or "") if isinstance(s, dict) else str(s)
        if n and n not in names: names.append(n)
    return names[:8]
fx = json.load(open(FIX, encoding="utf-8"))
ex = json.load(open(EXTRACT, encoding="utf-8"))
items = []
for it in fx["items"]:
    tier = FIXTURE.get(it["id"])
    if tier is None or it.get("kind") not in ("news", None): continue
    items.append({"id": f"F{it['id']}", "title": it["title"], "summary": (it.get("summary") or "")[:600],
                  "outlets": outlets_of(it.get("sources")), "tier": tier, "editor_label": it["label"], "lane": "newsdesk"})
tt = {f"T{r['id']}": r for r in ex["tt_topics"]}; nd = {f"N{r['id']}": r for r in ex["nd_events"]}
for k, tier in NEW.items():
    r = tt.get(k) or nd.get(k)
    if not r: print("missing", k); continue
    items.append({"id": k, "title": r["title"], "summary": (r.get("summary") or "")[:600], "outlets": outlets_of(r.get("sources")),
                  "tier": tier, "editor_label": None, "lane": "trending" if k[0] == "T" else "newsdesk",
                  "prod_status": r.get("status"), "peak": r.get("peak_score") or r.get("virality")})
json.dump({"version": "eval_v1", "built": "2026-11-25", "label_source": "Claude, desk importance tiers; owner to review",
           "items": items}, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
from collections import Counter
print(len(items), Counter(i["tier"] for i in items))
