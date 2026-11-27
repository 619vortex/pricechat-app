"""Build the Phase 1 eval set ops/intel/eval_v2.json = eval_v1 (215 items, same ids, labels and text) + new Claude-labelled items.

    python ops/intel/build_eval_v2.py ops/intel/eval_v1.json backend/tests/fixtures/nd_replay_7d.json EXTRACT.json ops/intel/eval_v2.json

EXTRACT.json comes from ops/intel/extract_news_backup.py (newest prod backup, headline/summary/outlet/timestamp/status fields of
nd_events news rows and tt_topics only; the DB copy is deleted). It is not committed; eval_v2.json holds the text it needs.

Sources of the new items:
  F<id>  News Desk replay fixture (11-16..11-23): news items not in v1, plus market/data alerts (numeric market moves);
         fixture editor labels (publish/went_out/covered/stay_out) are carried as editor_label where present.
  N<id>  nd_events news rows from the 2026-11-25 10:47 UTC backup, older (11-06..11-15) and newer (11-23..11-25) than v1's window.
  T<id>  Trending topics first seen 11-24 10:47 .. 11-25 (after v1's extract).
Each label is "TIER tag tag ..." (tags mark the hard-case kind, see TAGS). Every new label is labeller "claude" (owner to review).
Items whose normalised headline already appears in v1 or earlier in v2 are dropped (printed), so no headline is scored twice.

Split: ~30% "test" (held out: do not inspect model outputs on it while tuning), the rest "dev". Deterministic: stratified by tier,
ordered by sha1(group), where group is a story cluster for the clusters below (whole cluster on one side) or the item id.
The v1 items whose model outputs were inspected in Phase 0 (the published missed majors) are pinned to dev, with their clusters.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter, defaultdict

RUBRIC = """Tier = how a markets-and-world news wire (crypto traders, market watchers, world-news followers) should treat THIS item on its
own, judged from the headline + summary as given (no story memory; repeats are the dedup layer's job, so a re-report of an event is
tiered by what it states, while reaction/analysis pieces about a known event are N or X).
M major: would lead a wire's front page now. War turning point or a strike on NATO/Gulf capitals and state assets, direct US-Iran
  exchange of fire, mass casualties (dozens), central-bank surprise, index move >=4% (or a weekly/bear-market milestone of that size),
  crypto >=10%, top-10 company >=10%, deal/failure of tens of billions, global outage, hack >=$100M, a first-ever summit outcome.
S significant: newsworthy today with a concrete stated consequence. Takeover >=$1B, listed company >=15% (large caps >=8%), CEO ousted
  with price effect, IPO pricing/breaking issue price, sanction/tariff taking effect, regulator suit/charges/probe of a major company,
  index 2-4%, crypto 5-10% or a multi-month level, gold/silver/oil multi-percent day or a 12-month low, deaths in an attack,
  attacks on ships/Gulf states, confirmed hacks/exploits that halt a platform, national disruption.
N notable: real but routine. Strikes inside an established war with few casualties, small hacks (<$20M) and their follow-ups, fund
  flows, analyst moves, statements by officials with a market angle, meetings without outcome, hedged plans, small price moves.
X noise: no concrete consequence. Opinion/analysis/explainers/previews/live blogs/round-ups of several stories, warnings, threats,
  rumours, calls for action, sport, celebrity, bare search terms, tiny liquidation prints, clickbait.
Hard-case rules used: an official or government ATTRIBUTION of an event that happened ("..., says Tusk", "Polish govt says") is tiered
by the event, not as a statement; a hedge in a trailing clause does not hedge the lead fact; "reportedly/report" on a concrete event
lowers at most one tier; a round-up headline joining unrelated stories is X even if one part is big; numeric market alerts are tiered by
the size rules above."""

TAGS = {"att": "attributed claim", "hedge": "hedged wording", "round": "round-up", "num": "market move with numbers",
        "war": "conflict/attack", "crypto": "crypto protocol/exchange incident", "op": "opinion/analysis dressed as news",
        "rep": "repeat/follow-up of a story already in the set"}

FIXTURE = {  # replay fixture (news, market, data) -> "TIER tags"
 513: "M war", 515: "M war rep", 536: "M war rep att", 599: "M war rep", 533: "S war att", 553: "S war hedge", 593: "S war",
 596: "S war", 581: "S war att", 597: "S war att", 604: "S war att", 763: "S war att rep", 805: "S war att rep", 822: "S att",
 654: "M war", 669: "M war att", 662: "S war att", 684: "S war", 707: "S war att", 800: "S war", 826: "S war", 1119: "S war att",
 799: "M war", 804: "M war rep", 907: "M war rep", 784: "S war", 984: "S war", 1007: "S war att",
 811: "M", 776: "N att", 810: "N", 794: "X att",
 925: "M war att", 956: "M war", 964: "M war", 1023: "M war att", 974: "S war att",
 1009: "M num", 1018: "M num", 1020: "S num", 1004: "S num", 928: "S num", 860: "N", 1081: "N", 1126: "N",
 1052: "S num crypto", 975: "S num", 961: "S num", 882: "S num", 926: "N num", 1111: "N num", 519: "N", 518: "S num",
 973: "S crypto rep", 1003: "S war", 685: "S", 1131: "S", 1141: "N war", 896: "S war rep", 908: "S war rep", 841: "S war",
 828: "S war att", 906: "S war rep", 920: "S war", 922: "S war", 1086: "N war", 1091: "N war", 913: "X war", 802: "N war",
 573: "N war", 1089: "S war", 1075: "S war att", 1117: "S war hedge", 1136: "S war att", 1113: "N war hedge", 1104: "N rep",
 1128: "N", 1069: "S att", 1076: "S hedge", 1022: "N att", 1082: "X att", 1090: "N rep hedge",
 1100: "N crypto hedge", 1096: "N crypto", 1121: "N crypto", 1028: "N crypto", 1087: "N crypto", 540: "S crypto", 544: "N crypto",
 726: "N crypto", 783: "N crypto", 603: "S crypto", 634: "N crypto", 751: "N crypto", 779: "N crypto rep", 1150: "N crypto",
 1078: "N crypto rep", 1110: "N crypto", 694: "N crypto rep", 788: "N crypto rep", 782: "N crypto rep", 982: "N crypto rep",
 793: "N rep", 814: "S rep", 621: "S", 649: "S", 667: "N rep", 658: "N", 650: "N rep", 614: "N",
 541: "S", 543: "S", 607: "S", 796: "N", 583: "S", 560: "S", 868: "S", 889: "N rep", 770: "N", 1029: "S",
 787: "S", 786: "S", 769: "S num", 844: "S num", 849: "S", 991: "S", 753: "S hedge", 803: "S", 717: "N hedge", 829: "N", 806: "N",
 934: "N", 983: "N", 895: "N", 899: "N", 704: "N", 548: "N", 1065: "N", 1083: "N", 898: "N",
 720: "X round", 807: "X round", 894: "N round",
 591: "X hedge", 594: "X", 616: "X att", 638: "X op", 641: "X op", 672: "X op", 675: "X op", 985: "X op att", 1016: "X op att",
 1073: "X op", 1074: "X hedge", 936: "X hedge att", 955: "X op", 1000: "X hedge", 848: "X op", 912: "X op", 933: "X op", 923: "X op",
 909: "X op", 940: "X op", 968: "X", 839: "X", 842: "X op", 815: "X op", 837: "X op", 854: "X att", 871: "X", 892: "X op",
 1017: "X op", 1026: "X op", 1067: "X op", 1084: "X op", 497: "X op", 501: "X op", 507: "X att", 516: "X att", 525: "X op",
 531: "X hedge att", 549: "X att", 550: "X op", 551: "X", 554: "X op", 574: "X", 577: "X op", 580: "X op", 589: "X op", 609: "X op",
 617: "X", 637: "X", 659: "X op", 679: "X att", 680: "X att", 692: "X hedge att", 699: "X op", 702: "X att", 719: "X att",
 724: "X att", 727: "X", 736: "X att", 744: "N", 752: "X att", 759: "X hedge att", 792: "X", 797: "X att", 808: "X op", 809: "X",
 555: "N att", 612: "N att", 705: "N att",
 520: "N", 524: "N hedge", 526: "N att", 529: "N", 532: "N att", 534: "N", 546: "N crypto", 547: "N att", 563: "N", 564: "N crypto",
 566: "N", 567: "N", 571: "N", 575: "N", 576: "N att", 582: "N", 584: "N", 595: "N", 605: "N", 606: "N", 611: "N", 613: "N war",
 620: "N", 622: "N", 626: "N", 635: "N", 636: "N", 646: "N", 651: "N", 652: "N", 670: "N", 676: "N", 677: "N", 678: "N", 689: "N",
 690: "N war", 691: "N", 693: "N", 697: "N", 698: "N", 700: "N", 725: "N hedge", 731: "N", 737: "N att", 742: "N", 746: "N",
 749: "N", 750: "N hedge", 754: "S num", 758: "N rep", 765: "N", 766: "N", 795: "N", 825: "N", 827: "S", 847: "N att", 851: "N",
 867: "N num", 869: "N att", 872: "N num", 873: "N", 883: "N hedge", 893: "N", 911: "N num", 935: "N", 965: "N num", 971: "N",
 972: "N att", 979: "N num", 980: "X hedge", 1001: "S hedge", 1024: "N num", 1112: "N war", 1124: "N att", 1132: "N hedge",
 1138: "N num", 1160: "N", 496: "N",
 # market / data alerts (numeric). 1045 ($10.1B BTCUSD_PERP liquidation in an hour) is excluded: likely a contract-unit error.
 932: "M num", 1008: "M num", 929: "M num", "log-us-stocks": "M num", "log-nvda": "M num", "log-gold": "S num",
 "log-silver": "S num", 1046: "S num", 941: "S num", 875: "S num", 880: "S num", 812: "S num", 1088: "S num", 743: "S num",
 506: "N num", 509: "S num", 996: "S num", 930: "S num", 943: "S num", 850: "N num", 498: "N num", 1071: "N num", 495: "X num",
 499: "X num", 535: "X num", 562: "N num", 862: "N num", 633: "N num", 997: "N num", 1125: "N num", 623: "X num", 528: "X num",
}
ND = {  # nd_events news rows -> "TIER tags"
 2: "M num", 1: "N op crypto rep", 6: "S", 16: "X op", 17: "N crypto", 20: "N", 43: "S", 63: "N crypto", 71: "N", 73: "N att",
 74: "N crypto", 79: "S", 89: "N", 102: "N att hedge", 104: "N", 105: "X round", 106: "S war", 107: "X op", 108: "N", 110: "S",
 111: "S num", 112: "S", 114: "X att", 124: "S num", 133: "S", 134: "N num crypto", 135: "N", 136: "N", 137: "S", 139: "N",
 140: "X att", 141: "N", 142: "N hedge", 144: "X op", 145: "N", 146: "N att", 147: "S", 148: "X", 150: "X op", 151: "N",
 152: "X op", 154: "N", 156: "S", 160: "X op", 161: "S att", 163: "N crypto", 165: "X att", 166: "X round", 167: "N crypto",
 168: "N crypto", 169: "S war", 172: "S war", 173: "N", 174: "N round", 175: "N hedge att", 176: "N hedge", 177: "N", 178: "X op",
 179: "N", 180: "N", 181: "N", 182: "S num", 183: "N", 184: "N", 185: "X att", 190: "N", 192: "N", 193: "S num", 194: "X",
 195: "X hedge att", 196: "N att", 197: "N crypto", 198: "X att", 199: "X op", 200: "X att", 201: "S", 202: "X att", 203: "N",
 212: "X att", 213: "N", 214: "X op", 215: "S war", 216: "N", 218: "N", 219: "X", 220: "N att", 222: "X op hedge", 224: "N att",
 225: "X att", 230: "S war", 231: "N", 240: "N", 241: "X att", 243: "N", 246: "N hedge", 247: "N hedge", 255: "X op att",
 257: "N att", 260: "N", 263: "N", 264: "S hedge", 265: "X", 268: "N", 269: "X op", 270: "S", 271: "N", 273: "N num", 274: "S",
 276: "S", 281: "N", 285: "N num", 299: "X op", 306: "N", 308: "X", 309: "N", 310: "X", 311: "N", 316: "S num crypto",
 319: "N num", 320: "N round", 325: "N att hedge", 327: "N", 328: "X", 329: "N hedge", 330: "S war", 331: "N", 333: "N",
 335: "X op", 339: "X", 340: "S war att", 342: "N war", 343: "N", 348: "S num", 349: "N war att", 352: "X att", 353: "N hedge att",
 357: "N att", 358: "N", 359: "X", 362: "S", 363: "X op", 364: "X op", 365: "X", 367: "N", 371: "N att", 374: "X", 386: "N",
 389: "N", 390: "N", 391: "X op", 407: "X op", 415: "X round", 418: "X att", 443: "S num", 456: "S war", 460: "S num", 461: "N",
 471: "S war", 475: "S war", 478: "N num", 479: "X op", 482: "N hedge", 483: "N att", 484: "X op", 487: "S", 490: "N att",
 493: "X",
 1146: "X hedge att", 1148: "N att", 1151: "N", 1153: "X hedge", 1154: "X att", 1155: "N crypto rep", 1220: "X op hedge",
 1224: "N war", 1227: "S num", 1231: "N", 1232: "N", 1233: "N", 1235: "N num", 1238: "X op", 1239: "S war", 1240: "X att",
 1242: "S war att", 1243: "S war rep", 1247: "N", 1249: "N", 1250: "N hedge", 1256: "N crypto", 1257: "N", 1258: "N crypto",
 1259: "X att", 1260: "X op", 1261: "N", 1263: "N", 1265: "S war", 1266: "X att", 1267: "X att", 1268: "X att", 1269: "X att",
 1270: "X att", 1275: "N", 1276: "S", 1277: "N", 1278: "N att", 1279: "N att", 1283: "N att hedge", 1291: "X att", 1292: "S war",
 1293: "X att", 1294: "N hedge", 1295: "S war", 1296: "S war att",
}
TT = {  # tt_topics first seen after v1's extract -> "TIER tags"
 46965: "S", 46465: "M hedge", 46956: "S", 47161: "N", 46997: "S num", 46911: "X att", 46711: "X att", 47092: "X", 46586: "X",
 46548: "X hedge", 46568: "X", 46742: "X", 46757: "X hedge", 46705: "X", 46958: "N", 46588: "S num", 46858: "X", 47274: "X",
 46605: "N", 46614: "N num", 46839: "X op", 46991: "N hedge", 46924: "X", 46856: "N", 46591: "X", 46860: "S num", 46600: "X op",
 47257: "S num", 46903: "X op", 46928: "X op", 46843: "X", 46562: "N", 47072: "N", 46916: "X", 46630: "X op", 46782: "X hedge",
 47203: "S hedge", 46985: "X", 47158: "X round", 47172: "N crypto", 46921: "N", 46972: "N num", 47200: "X", 46567: "N",
 46971: "X op crypto", 47107: "N", 47075: "N", 47124: "X", 47234: "X", 46655: "X", 46649: "N hedge", 46734: "N", 46900: "X att",
 46983: "N", 47039: "X op", 47126: "S", 47222: "X", 46834: "N", 46759: "M num op", 46646: "X op", 46566: "X op", 47058: "X",
 47088: "X", 46544: "X", 46680: "N num", 46593: "N", 47202: "X", 46848: "X att", 47076: "S num", 47094: "N", 47175: "X round",
 47197: "N crypto", 46962: "X hedge",
}
CLUSTERS = {  # story clusters kept on one side of the split
 "poland_missile": ["T44161", "F969", "N1184", "F925", "F956", "F964", "F1023", "F974"],
 "crimea_bridge": ["F513", "F515", "F536", "F599", "F533", "F604", "F763", "F805", "F596", "F593", "F822"],
 "riyadh_tower": ["F799", "F804", "F907", "N1243"],
 "nasdaq_crash": ["F1002", "T44558", "T44520", "F1009", "F1018", "F1020", "F932", "F1088", "F1004", "F928", "T46759"],
 "crowdstrike_zscaler": ["F1173", "T45684"],
 "anthropic_ipo_day": ["F830", "F866", "F790", "T43559", "F787", "F786", "F769", "F844", "F849", "F803"],
 "huang_return": ["F1162", "T45813", "T45810", "Flog-nvda", "Flog-us-stocks", "Ftrend-45827"],
 "hormuz_mines": ["N1211"],
 "us_bank_outage": ["F578"],
 "france_troops": ["F645"],
}
PINNED_DEV = {"T44161", "F645", "F830", "F866", "F969", "F1002", "F1162", "F1173", "T45684", "N1211", "F578"}  # inspected in Phase 0
TEST_FRAC = 0.30


def outlets_of(sources):
    try:
        src = json.loads(sources) if isinstance(sources, str) else (sources or [])
    except Exception:
        src = []
    names = []
    for s in src if isinstance(src, list) else []:
        n = (s.get("name") or s.get("source") or s.get("outlet") or "") if isinstance(s, dict) else str(s)
        if n and n not in names:
            names.append(n)
    return names[:8]


def norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def parse(label: str) -> tuple[str, list[str]]:
    tier, *tags = label.split()
    assert tier in "MSNX" and len(tier) == 1, label
    assert all(t in TAGS for t in tags), label
    return tier, tags


def main() -> None:
    v1_path, fix_path, ex_path, out = sys.argv[1:5]
    v1 = json.load(open(v1_path, encoding="utf-8"))
    fx = {it["id"]: it for it in json.load(open(fix_path, encoding="utf-8"))["items"]}
    ex = json.load(open(ex_path, encoding="utf-8"))
    nd = {r["id"]: r for r in ex["nd_events"]}
    tt = {r["id"]: r for r in ex["tt_topics"]}
    items = [{**i, "labeller": "claude", "origin": "v1", "tags": []} for i in v1["items"]]
    seen = {norm(i["title"]) for i in items}
    dropped = []

    def add(iid, title, summary, outlets, label, lane, **extra):
        tier, tags = parse(label)
        if norm(title) in seen:
            dropped.append((iid, title))
            return
        seen.add(norm(title))
        summary = (summary or "").strip()
        if summary in (title, "Data: public exchange liquidation feeds.") or summary.startswith("Price from "):
            summary = ""  # feed boilerplate, not context
        items.append({"id": iid, "title": title, "summary": summary[:600], "outlets": outlets, "tier": tier, "tags": tags,
                      "labeller": "claude", "origin": "v2", "lane": lane, **extra})

    for k, label in FIXTURE.items():
        it = fx[k]
        add(f"F{k}", it["title"], it.get("summary"), outlets_of(it.get("sources")), label,
            "newsdesk" if it.get("kind") == "news" else "market", editor_label=it.get("label"), kind=it.get("kind"),
            at=it.get("first_seen") or it.get("created_at"))
    for k, label in ND.items():
        r = nd[k]
        add(f"N{k}", r["title"], r.get("summary"), r.get("outlets") or [], label, "newsdesk", editor_label=None,
            prod_status=r.get("status"), at=r.get("created_at"))
    for k, label in TT.items():
        r = tt[k]
        add(f"T{k}", r["title"], r.get("summary"), r.get("outlets") or [], label, "trending", editor_label=None,
            prod_status=r.get("status"), peak=r.get("peak_score"), at=r.get("first_seen"))

    # split
    group = {}
    for name, ids in CLUSTERS.items():
        for i in ids:
            group[i] = name
    pinned_groups = {group.get(i, i) for i in PINNED_DEV}
    groups = defaultdict(list)
    for it in items:
        groups[group.get(it["id"], it["id"])].append(it)
    tiers = Counter(it["tier"] for it in items)
    want = {t: round(TEST_FRAC * n) for t, n in tiers.items()}
    got = Counter()
    for g in sorted(groups, key=lambda g: hashlib.sha1(f"eval_v2:{g}".encode()).hexdigest()):
        need = Counter(it["tier"] for it in groups[g])
        side = "test" if g not in pinned_groups and all(got[t] + n <= want[t] + 1 for t, n in need.items()) else "dev"
        if side == "test":
            got.update(need)
        for it in groups[g]:
            it["split"] = side
    meta = {"version": "eval_v2", "built": "2026-11-25", "base": "eval_v1 (215 items, unchanged ids/labels/text)",
            "label_source": "Claude (labeller field); owner to review", "rubric": RUBRIC, "tags": TAGS,
            "split": f"~{int(TEST_FRAC * 100)}% test held out (stratified by tier, sha1 of story cluster/id; Phase-0-inspected items pinned to dev)",
            "clusters": CLUSTERS}
    json.dump({**meta, "items": items}, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for iid, t in dropped:
        print("dropped duplicate headline", iid, t[:80])
    print(len(items), "items;", dict(Counter(i["tier"] for i in items)), "test:", dict(Counter(i["tier"] for i in items if i["split"] == "test")),
          "new:", sum(i["origin"] == "v2" for i in items))


if __name__ == "__main__":
    main()
