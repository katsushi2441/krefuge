#!/usr/bin/env python3
"""市区町村ごとの避難所の統計を作る（地域ページの中身）。

`shelters.muni` は22%がNULL（元データが市区町村を持たない自治体がある）なので、
`address` から scripts/muni.py で市区町村を決めて補う。muni_vintage にある
first_published / last_updated も市区町村ページに載せる（データの鮮度の開示）。

  cd /home/kojima/work/krefuge && /usr/bin/python3 scripts/build_muni_stats.py
"""
import os, sys, json, sqlite3, collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from muni import load_canon, extract, PREFS

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "krefuge.db")
CODE_BY_PREF = {p: f"{i+1:02d}" for i, p in enumerate(PREFS)}
HAZARDS = ("flood", "landslid", "surge", "quake", "tsunami", "bigfire", "inlflood", "volcano")

DDL = """
CREATE TABLE IF NOT EXISTS muni_stats (
  muni_code TEXT PRIMARY KEY, pref_code TEXT, pref TEXT, muni TEXT,
  shelters INTEGER, flood INTEGER, landslid INTEGER, surge INTEGER, quake INTEGER,
  tsunami INTEGER, bigfire INTEGER, inlflood INTEGER, volcano INTEGER,
  first_published TEXT, last_updated TEXT, samples TEXT
);
CREATE INDEX IF NOT EXISTS muni_stats_pref ON muni_stats(pref_code);
"""


def main():
    pref_canon, muni_canon = load_canon(DB)
    con = sqlite3.connect(DB)
    con.executescript(DDL)
    vint = {c: (f, l) for c, f, l in con.execute(
        "SELECT muni_code, first_published, last_updated FROM muni_vintage")}

    agg, miss = {}, collections.Counter()
    q = ("SELECT pref, muni, name, address, " + ",".join(HAZARDS) + " FROM shelters")
    for row in con.execute(q):
        pref, muni, name, addr = row[0], row[1], row[2], row[3]
        pc = CODE_BY_PREF.get(pref)
        if not pc:
            miss["県名不明"] += 1
            continue
        # muni 列があってもコードは持っていないので、住所から引いて団体コードを得る
        hit = extract(pc, addr, muni_canon) or (extract(pc, (muni or "") + "　", muni_canon) if muni else None)
        if not hit:
            miss[pref] += 1
            continue
        mname, code = hit
        a = agg.get(code)
        if a is None:
            f, l = vint.get(code, (None, None))
            a = agg[code] = dict(pref_code=pc, pref=pref, muni=mname, shelters=0,
                                 first_published=f, last_updated=l, samples=[],
                                 **{h: 0 for h in HAZARDS})
        a["shelters"] += 1
        for i, h in enumerate(HAZARDS):
            if row[4 + i]:
                a[h] += 1
        if len(a["samples"]) < 6 and name:
            a["samples"].append({"name": name, "address": addr})

    con.execute("DELETE FROM muni_stats")
    cols = ["muni_code", "pref_code", "pref", "muni", "shelters", *HAZARDS,
            "first_published", "last_updated", "samples"]
    con.executemany(
        f"INSERT INTO muni_stats ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        [(code, a["pref_code"], a["pref"], a["muni"], a["shelters"], *[a[h] for h in HAZARDS],
          a["first_published"], a["last_updated"], json.dumps(a["samples"], ensure_ascii=False))
         for code, a in agg.items()])
    con.commit()
    n = sum(miss.values())
    print(f"市区町村 {len(agg):,} / 住所から特定できず {n:,}")
    if n:
        print("  内訳:", dict(miss.most_common(5)))
    for r in con.execute("SELECT pref,muni,shelters,flood,tsunami,quake FROM muni_stats ORDER BY shelters DESC LIMIT 5"):
        print(f"   {r[0]}{r[1]}: 施設{r[2]:,} 洪水{r[3]:,} 津波{r[4]:,} 地震{r[5]:,}")
    con.close()


if __name__ == "__main__":
    main()
