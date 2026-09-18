#!/usr/bin/env python3
"""政令市の区ごとに、避難所の統計を作る（区ページの中身）。

**なぜ要るか**: 指定緊急避難場所データの市区町村は政令市を1件にまとめている。
名古屋市は1,750件が「名古屋市」の1ページに入っていて、区では引けなかった。
一方 kflood は区ページを持っていて「名古屋市北区 ハザードマップ 洪水」で
9〜13位に入っている（2026-09-19 実測）。同じ粒度をこちらにも用意する。

区は住所から取る。`名古屋市○区` は住所文字列にそのまま入っているので、
推測は要らない（名古屋の1,750件のうち区が取れないのは17件）。

  cd /home/kojima/work/krefuge && /usr/bin/python3 scripts/build_ward_stats.py
"""
import json
import os
import re
import sqlite3

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "krefuge.db")
HAZARDS = ("flood", "landslid", "surge", "quake", "tsunami", "bigfire", "inlflood", "volcano")

# 対象の政令市。slug は kflood の区ページと同じ綴りにそろえる。
CITIES = {
    "23100": {
        "city": "名古屋市", "pref": "愛知県", "city_slug": "aichi-nagoya",
        "wards": {"千種区": "chikusa", "東区": "higashi", "北区": "kita", "西区": "nishi",
                  "中村区": "nakamura", "中区": "naka", "昭和区": "showa", "瑞穂区": "mizuho",
                  "熱田区": "atsuta", "中川区": "nakagawa", "港区": "minato", "南区": "minami",
                  "守山区": "moriyama", "緑区": "midori", "名東区": "meito", "天白区": "tempaku"},
    },
}

DDL = """
CREATE TABLE IF NOT EXISTS ward_stats (
  city_slug TEXT, ward_slug TEXT, muni_code TEXT, pref TEXT, city TEXT, ward TEXT,
  shelters INTEGER, flood INTEGER, landslid INTEGER, surge INTEGER, quake INTEGER,
  tsunami INTEGER, bigfire INTEGER, inlflood INTEGER, volcano INTEGER, samples TEXT,
  PRIMARY KEY (city_slug, ward_slug)
);
"""


def main() -> None:
    con = sqlite3.connect(DB)
    con.executescript(DDL)
    con.execute("DELETE FROM ward_stats")
    total = 0
    for code, c in CITIES.items():
        pat = re.compile(re.escape(c["city"]) + r"(.{1,3}?区)")
        agg: dict[str, dict] = {}
        q = "SELECT name, address, " + ",".join(HAZARDS) + " FROM shelters WHERE address LIKE ?"
        for row in con.execute(q, ("%" + c["city"] + "%",)):
            name, addr = row[0], row[1]
            m = pat.search(addr or "")
            if not m or m.group(1) not in c["wards"]:
                continue          # 区が読めない住所は数えない。当てずっぽうで振り分けない
            w = agg.setdefault(m.group(1), {"n": 0, "h": dict.fromkeys(HAZARDS, 0), "s": []})
            w["n"] += 1
            for i, h in enumerate(HAZARDS):
                if row[2 + i]:
                    w["h"][h] += 1
            if len(w["s"]) < 6:
                w["s"].append({"name": name, "address": addr})
        for ward, slug in c["wards"].items():
            d = agg.get(ward)
            if not d:
                continue
            con.execute(
                "INSERT INTO ward_stats (city_slug,ward_slug,muni_code,pref,city,ward,shelters,"
                + ",".join(HAZARDS) + ",samples) VALUES (" + ",".join("?" * (7 + len(HAZARDS) + 1)) + ")",
                [c["city_slug"], slug, code, c["pref"], c["city"], ward, d["n"]]
                + [d["h"][h] for h in HAZARDS] + [json.dumps(d["s"], ensure_ascii=False)])
            total += d["n"]
            print(f"  {c['city']}{ward}: {d['n']:,}件")
    con.commit()
    con.close()
    print(f"ward_stats を作りました（計 {total:,}件）")


if __name__ == "__main__":
    main()
