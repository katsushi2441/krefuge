#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""指定緊急避難場所データ(国土地理院)を SQLite に取り込む。

なぜ PostGIS を使わないか:
  収録されるのは点だけ(全国115,447件)で、面の包含判定が要らない。
  R-tree で最近傍を引けば足りるので、Docker も Postgres も不要にした。
  買い切り版の設置難度が下がる(khazard は PostGIS が必要だった)。

khazard から引き継ぐ原則:
  データ時点が分からないデータでは判定しない。
  「いつ時点か」を答えと一緒に必ず出せるよう、datasets 表に出典と時点を記録する。

出典: 国土地理院「指定緊急避難場所データ」(CC BY 4.0)
"""
import argparse
import csv
import json
import os
import sqlite3
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
DB = os.path.join(ROOT, "data", "krefuge.db")

SOURCE_URL = "https://www.geospatial.jp/ckan/dataset/hinanbasho"
ATTRIBUTION = "国土地理院「指定緊急避難場所データ」を加工して作成"
LICENSE = "CC BY 4.0"

# shapefile の列 -> 表示名。国土地理院の災害種別はこの8種で固定。
HAZARDS = [
    ("flood", "洪水"),
    ("landslid", "崖崩れ・土石流・地すべり"),
    ("surge", "高潮"),
    ("quake", "地震"),
    ("tsunami", "津波"),
    ("bigfire", "大規模な火事"),
    ("inlflood", "内水氾濫"),
    ("volcano", "火山現象"),
]

SCHEMA = """
CREATE TABLE datasets (
  name TEXT PRIMARY KEY,
  source_url TEXT NOT NULL,
  data_vintage TEXT NOT NULL,   -- 空を許さない。時点不明のデータでは判定しない
  attribution TEXT NOT NULL,
  license TEXT NOT NULL,
  loaded_at TEXT NOT NULL
);
CREATE TABLE shelters (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  address TEXT NOT NULL,
  pref TEXT,
  muni TEXT,
  lat REAL NOT NULL,
  lon REAL NOT NULL,
  flood INTEGER NOT NULL DEFAULT 0,
  landslid INTEGER NOT NULL DEFAULT 0,
  surge INTEGER NOT NULL DEFAULT 0,
  quake INTEGER NOT NULL DEFAULT 0,
  tsunami INTEGER NOT NULL DEFAULT 0,
  bigfire INTEGER NOT NULL DEFAULT 0,
  inlflood INTEGER NOT NULL DEFAULT 0,
  volcano INTEGER NOT NULL DEFAULT 0
);
CREATE VIRTUAL TABLE shelters_rtree USING rtree(id, min_lat, max_lat, min_lon, max_lon);
CREATE TABLE muni_vintage (
  muni_code TEXT PRIMARY KEY,
  muni_name TEXT NOT NULL,
  first_published TEXT,
  last_updated TEXT
);
CREATE INDEX idx_shelters_pref ON shelters(pref);
"""


def shp_to_csv(shp: str, out: str) -> None:
    """ogr2ogr で緯度経度つきCSVにする。GMLと違い Shapefile は素直に読める。

    ENCODING は指定しない。.cpg が UTF-8 を宣言しており、
    -lco ENCODING=UTF-8 を付けると二重変換で壊れる(2026-09-05 実測)。
    """
    subprocess.run(
        ["ogr2ogr", "-f", "CSV", out, shp, "-lco", "GEOMETRY=AS_XY"],
        check=True, capture_output=True,
    )


def load_muni_vintage(cur) -> int:
    """市町村別の公開日・最終更新日。判定結果に「いつ時点か」を出すための土台。"""
    path = os.path.join(DATA, "publicHistoryListData.csv")
    if not os.path.exists(path):
        return 0
    n = 0
    with open(path, encoding="utf-8", newline="") as f:
        # 改行が CR のみ。splitlines() で吸収する
        for line in f.read().replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            parts = line.split(",")
            if len(parts) < 4 or not parts[0].strip():
                continue
            cur.execute(
                "INSERT OR REPLACE INTO muni_vintage VALUES (?,?,?,?)",
                (parts[0].strip(), parts[1].strip(), parts[2].strip() or None,
                 parts[3].strip() or None),
            )
            n += 1
    return n


def split_address(addr: str):
    """住所を都道府県と市区町村に割る。市町村別の時点を引くのに使う。"""
    for pref in ("北海道", "東京都", "大阪府", "京都府"):
        if addr.startswith(pref):
            rest = addr[len(pref):]
            break
    else:
        i = addr.find("県")
        if i < 0:
            return None, None
        pref, rest = addr[:i + 1], addr[i + 1:]
    for mark in ("市", "郡", "区"):
        j = rest.find(mark)
        if j >= 0:
            return pref, rest[:j + 1]
    return pref, None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shp", default=os.path.join(DATA, "zenkoku", "00_全国", "all.shp"))
    ap.add_argument("--vintage", required=True,
                    help="データ時点 (YYYY-MM-DD)。不明なまま取り込ませない")
    args = ap.parse_args()

    if not os.path.exists(args.shp):
        sys.exit(f"shapefile が無い: {args.shp}")

    with tempfile.TemporaryDirectory() as td:
        csv_path = os.path.join(td, "all.csv")
        shp_to_csv(args.shp, csv_path)

        if os.path.exists(DB):
            os.remove(DB)
        con = sqlite3.connect(DB)
        cur = con.cursor()
        cur.executescript(SCHEMA)

        nv = load_muni_vintage(cur)

        csv.field_size_limit(1 << 20)
        rows = 0
        # errors="replace": 元データに254バイト打ち切りで日本語が半分に切れた箇所がある
        # (2026-06-12版で実測。例: "…娯楽室" の直後が \xe3\x80 で終端)。
        # 使うのは施設名・住所・災害種別フラグだけなので、備考の欠けは判定に影響しない。
        with open(csv_path, encoding="utf-8", errors="replace", newline="") as f:
            for r in csv.DictReader(f):
                try:
                    lon, lat = float(r["X"]), float(r["Y"])
                except (TypeError, ValueError):
                    continue
                name = (r.get("evacsite") or "").strip()
                addr = (r.get("address") or "").strip()
                if not name or not addr:
                    continue
                pref, muni = split_address(addr)
                flags = [1 if (r.get(k) or "").strip() else 0 for k, _ in HAZARDS]
                rows += 1
                cur.execute(
                    "INSERT INTO shelters (id,name,address,pref,muni,lat,lon,"
                    "flood,landslid,surge,quake,tsunami,bigfire,inlflood,volcano)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [rows, name, addr, pref, muni, lat, lon] + flags,
                )
                cur.execute(
                    "INSERT INTO shelters_rtree VALUES (?,?,?,?,?)",
                    (rows, lat, lat, lon, lon),
                )

        cur.execute(
            "INSERT INTO datasets VALUES (?,?,?,?,?,datetime('now','localtime'))",
            ("指定緊急避難場所データ", SOURCE_URL, args.vintage, ATTRIBUTION, LICENSE),
        )
        con.commit()

        n_pref = cur.execute("SELECT COUNT(DISTINCT pref) FROM shelters").fetchone()[0]
        print(f"  取り込み: {rows:,}件 / 都道府県 {n_pref} / 市町村時点 {nv}件")
        for k, label in HAZARDS:
            c = cur.execute(f"SELECT COUNT(*) FROM shelters WHERE {k}=1").fetchone()[0]
            print(f"    {label:<22} {c:>7,}")
        con.close()


if __name__ == "__main__":
    main()
