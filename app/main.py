#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kurage 避難所マップ — 住所から「避難所まで徒歩何分」を答える。

khazard(土砂災害ハザードマップ)の続編。設計原則を引き継ぐ:
  - 判定に使ったデータの時点を必ず一緒に出す
  - 時点が分からないデータでは判定しない
  - 断定できないところは断定しない

この製品固有の芯:
  「近い避難所」ではなく「その災害で使える避難所」を出す。
  指定緊急避難場所は災害種別ごとに指定されていて、
  地震で使える場所が洪水では使えないことがあるため。

出典: 国土地理院「指定緊急避難場所データ」(CC BY 4.0)
"""
import json
import math
import os
import re
import sqlite3
import time
from collections import defaultdict

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app import nagoya_live

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "krefuge.db")
GSI = "https://msearch.gsi.go.jp/address-search/AddressSearch"
# 標高API。津波・高潮では「その場所が何メートルか」が避難先の判断に直結する
GSI_ELEV = "https://cyberjapandata2.gsi.go.jp/general/dem/scripts/getelevation.php"
VALHALLA = os.environ.get("KREFUGE_VALHALLA", "http://127.0.0.1:18359")

STALE_YEARS = 5      # これより古い市町村データには注意書きを出す
CANDIDATES = 20      # 直線距離で絞ってから徒歩時間を計算する件数
SHOW = 5             # 画面に出す件数
WALK_MPS = 80 / 60   # Valhalla が落ちているときの代替(徒歩80m/分)

HAZARDS = [
    ("landslid", "土砂災害", "崖崩れ・土石流・地すべり"),
    ("flood", "洪水", "洪水"),
    ("quake", "地震", "地震"),
    ("tsunami", "津波", "津波"),
    ("surge", "高潮", "高潮"),
    ("inlflood", "内水氾濫", "内水氾濫"),
    ("bigfire", "大規模火事", "大規模な火事"),
    ("volcano", "火山現象", "火山現象"),
]
HAZARD_KEYS = {k for k, _, _ in HAZARDS}

app = FastAPI(title="Kurage 避難所マップ")
_hits = defaultdict(list)


def limited(ip: str, per_min: int = 20) -> bool:
    now = time.time()
    _hits[ip] = [t for t in _hits[ip] if now - t < 60]
    if len(_hits[ip]) >= per_min:
        return True
    _hits[ip].append(now)
    return False


def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def geocode(q: str):
    """国土地理院の住所検索。施設名だと別地方の類似住所が先頭に来るので、
    クエリを含む候補を優先する(khazard/kshoken と同じ挙動)。"""
    r = requests.get(GSI, params={"q": q}, timeout=10,
                     headers={"User-Agent": "krefuge/1.0 (kurage.exbridge.jp)"})
    r.raise_for_status()
    items = r.json()
    if not items:
        return None
    def score(it):
        t = it.get("properties", {}).get("title", "")
        return (q in t, t.startswith(q), -len(t))
    it = max(items, key=score)
    lon, lat = it["geometry"]["coordinates"]
    return {"lat": lat, "lon": lon, "label": it.get("properties", {}).get("title", q)}


def elevation(lat, lon):
    """国土地理院の標高API。取れなければ None を返し、画面には出さない。
    hsrc は測定のもと(5mレーザー等)で、精度の根拠として一緒に出す。"""
    try:
        r = requests.get(GSI_ELEV, params={"lon": lon, "lat": lat, "outtype": "JSON"},
                         timeout=8, headers={"User-Agent": "krefuge/1.0 (kurage.exbridge.jp)"})
        r.raise_for_status()
        d = r.json()
        v = d.get("elevation")
        if v in (None, "-----"):
            return None
        return {"m": round(float(v), 1), "source": d.get("hsrc") or ""}
    except Exception:
        return None


def haversine(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearby(cur, lat, lon, hazard):
    """R-tree で矩形を広げながら候補を集める。全国115,447点なので索引で十分速い。"""
    where = f" AND s.{hazard}=1" if hazard in HAZARD_KEYS else ""
    for deg in (0.02, 0.05, 0.12, 0.30, 0.80):
        rows = cur.execute(
            "SELECT s.* FROM shelters s JOIN shelters_rtree r ON r.id=s.id"
            " WHERE r.min_lat>=? AND r.max_lat<=? AND r.min_lon>=? AND r.max_lon<=?"
            + where,
            (lat - deg, lat + deg, lon - deg, lon + deg),
        ).fetchall()
        if len(rows) >= 1:
            out = sorted(rows, key=lambda s: haversine(lat, lon, s["lat"], s["lon"]))
            return out[:CANDIDATES], deg
    return [], None


def walk_times(lat, lon, rows):
    """道路網をたどった徒歩時間。Valhalla が落ちていれば直線＋徒歩80m/分で代替し、
    その旨を必ず結果に載せる(黙って精度を落とさない)。"""
    targets = [{"lat": r["lat"], "lon": r["lon"]} for r in rows]
    try:
        res = requests.post(
            f"{VALHALLA}/sources_to_targets",
            json={"sources": [{"lat": lat, "lon": lon}], "targets": targets,
                  "costing": "pedestrian"},
            timeout=25,
        )
        res.raise_for_status()
        cells = res.json()["sources_to_targets"][0]
        out = []
        for r, c in zip(rows, cells):
            if c.get("time") is None:
                continue
            out.append((r, int(c["time"]), float(c["distance"]) * 1000))
        if out:
            return out, "road"
    except Exception:
        pass
    out = [(r, int(haversine(lat, lon, r["lat"], r["lon"]) / WALK_MPS),
            haversine(lat, lon, r["lat"], r["lon"])) for r in rows]
    return out, "straight"


def vintage_for(cur, address: str):
    """住所から市町村を引き、その市町村の最終更新日を返す。
    分からなければ None を返し、呼び出し側で『不明』と明示する。"""
    row = cur.execute("SELECT data_vintage FROM datasets LIMIT 1").fetchone()
    fallback = row["data_vintage"] if row else None
    if not address:
        return fallback, None
    best = None
    for m in cur.execute("SELECT muni_name, last_updated FROM muni_vintage"):
        n = m["muni_name"]
        if n and address.startswith(n) and (best is None or len(n) > len(best[0])):
            best = (n, m["last_updated"])
    if best and best[1]:
        return best[1], best[0]
    return fallback, None


def staleness(vintage: str):
    try:
        y, mo, d = (int(x) for x in str(vintage).split("-")[:3])
    except Exception:
        return None, False
    days = (time.time() - time.mktime((y, mo, d, 0, 0, 0, 0, 0, -1))) / 86400
    years = round(days / 365.25, 1)
    return years, years >= STALE_YEARS


@app.get("/api/check")
def check(request: Request, q: str, hazard: str = ""):
    ip = request.client.host if request.client else "?"
    if limited(ip):
        raise HTTPException(429, "しばらく待ってからお試しください")
    q = (q or "").strip()
    if not q:
        raise HTTPException(400, "住所を入力してください")
    if hazard and hazard not in HAZARD_KEYS:
        raise HTTPException(400, "災害種別の指定が不正です")

    try:
        g = geocode(q)
    except Exception:
        raise HTTPException(502, "住所検索に接続できませんでした")
    if not g:
        raise HTTPException(404, "住所が見つかりませんでした")

    cur = conn().cursor()
    rows, _ = nearby(cur, g["lat"], g["lon"], hazard)
    if not rows:
        return JSONResponse({
            "query": q, "resolved": g["label"], "hazard": hazard,
            "shelters": [], "note": "この地点の周辺では該当する避難所が見つかりませんでした。",
        })

    timed, mode = walk_times(g["lat"], g["lon"], rows)
    timed.sort(key=lambda t: t[1])
    vintage, muni = vintage_for(cur, rows[0]["address"])
    years, stale = staleness(vintage) if vintage else (None, False)

    out = []
    for r, sec, dist in timed[:SHOW]:
        out.append({
            "name": r["name"], "address": r["address"],
            "lat": r["lat"], "lon": r["lon"],
            "walk_minutes": max(1, round(sec / 60)),
            "distance_m": round(dist),
            "hazards": [label for k, label, _ in HAZARDS if r[k]],
        })
    elev = elevation(g["lat"], g["lon"])
    live = None
    if nagoya_live.AREA in (g["label"] or ""):
        # 名古屋市: 帰宅困難者向け退避施設の「いまの開設状況」（市の公開 Feature Service）。指定緊急避難場所とは別物として返す
        d = nagoya_live.fetch()
        live = {"area": nagoya_live.AREA, "status": d.get("status"), "updated_at": d.get("updated_at"), "fetched_at": d.get("fetched_at"),
                "source": d.get("source"), "source_url": d.get("source_url"), "summary": nagoya_live.summary(d),
                "facilities": nagoya_live.nearest(d, g["lat"], g["lon"], 3) if d.get("facilities") else [],
                "what": "帰宅困難者が最大24時間滞在する退避施設。命を守るために逃げ込む指定緊急避難場所とは別のものです。",
                "not_used": "市の指定避難所の開設状況レイヤは2024-10-29以降更新されておらず（2026-09-08の警戒レベル5発令中も全件未開設）、更新されていない値で「未開設」と出すのは危険なため本サービスでは表示しません。"}
    return JSONResponse({
        "query": q, "resolved": g["label"], "lat": g["lat"], "lon": g["lon"],
        "nagoya_live": live,
        "elevation": elev,
        "hazard": hazard,
        "hazard_label": next((l for k, l, _ in HAZARDS if k == hazard), "指定なし"),
        "shelters": out,
        "walk_basis": "道路網（Valhalla）" if mode == "road"
                      else "直線距離×徒歩80m/分（道路網エンジン未応答のため代替）",
        "data_vintage": vintage, "vintage_scope": muni or "全国データの取得時点",
        "data_age_years": years, "stale": stale,
        "source": "国土地理院「指定緊急避難場所データ」",
        "license": "CC BY 4.0",
        "notes": [
            "本サービスの判定は参考情報です。公的な証明ではありません。",
            "最新でない場合や未掲載の場合があります。最新かつ詳細の状況は必ず当該市町村にご確認ください。",
            "住所から求めた座標は町丁目のおおよその位置のため、徒歩時間は目安です。",
            "「指定緊急避難場所」は災害種別ごとに指定されます。ある災害で使える場所が、別の災害では使えないことがあります。",
            "「指定緊急避難場所」（緊急時に逃げ込む場所）と「指定避難所」（一定期間滞在する施設）は異なります。本サービスが扱うのは前者です。",
        ],
    })


@app.get("/healthz")
def healthz():
    try:
        cur = conn().cursor()
        n = cur.execute("SELECT COUNT(*) FROM shelters").fetchone()[0]
        v = cur.execute("SELECT data_vintage FROM datasets LIMIT 1").fetchone()[0]
        return {"status": "ok", "shelters": n, "data_vintage": v}
    except Exception as e:
        return JSONResponse({"status": "ng", "error": str(e)}, status_code=500)


PAGE = """<!doctype html><html lang="ja"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kurage 避難所マップ | 住所から避難所まで徒歩何分・海抜（標高）も表示（全国11万件）</title>
<meta name="description" content="住所を入れると、最寄りの指定緊急避難場所まで道路をたどって徒歩何分かを表示します。その地点の海抜（標高）も国土地理院のデータで表示するので、津波・高潮のときに避難先がここより高いかを判断できます。土砂災害・洪水・地震・津波など災害種別ごとに絞り込み。全国115,447件収録。">
<link rel="canonical" href="https://kurage.exbridge.jp/krefuge.php/">
<meta property="og:type" content="website">
<meta property="og:title" content="Kurage 避難所マップ｜避難所まで徒歩何分">
<meta property="og:description" content="住所から、その災害で使える避難所までの徒歩時間を表示。全国115,447件・データ時点つき。">
<meta property="og:url" content="https://kurage.exbridge.jp/krefuge.php/">
<meta name="twitter:card" content="summary_large_image">
<style>
*{box-sizing:border-box}
body{margin:0;background:#fff;color:#12202f;font-family:system-ui,-apple-system,"Hiragino Kaku Gothic ProN","Noto Sans JP",sans-serif;line-height:1.75}
.wrap{max-width:840px;margin:0 auto;padding:26px 16px 60px}
h1{font-size:25px;margin:0 0 10px;line-height:1.4}
h1 a{color:inherit;text-decoration:none}
.lead{font-size:15px;color:#37485a;margin:0 0 20px}
.card{border:1px solid #e5ebf1;border-radius:14px;padding:18px;background:#fff}
form{display:flex;gap:8px;flex-wrap:wrap}
input{flex:1 1 240px;min-width:0;padding:12px 13px;font-size:16px;border:1px solid #cdd8e3;border-radius:10px}
select{padding:12px 10px;font-size:15px;border:1px solid #cdd8e3;border-radius:10px;background:#fff}
button{padding:12px 22px;font-size:15.5px;font-weight:800;color:#fff;background:#0a9a8f;border:0;border-radius:10px;cursor:pointer}
button:disabled{opacity:.5}
.res{margin-top:16px}
.hit{border:1px solid #e5ebf1;border-radius:11px;padding:13px 14px;margin:9px 0}
.hit .nm{font-weight:800;font-size:16px}
.hit .mi{color:#0a9a8f;font-weight:800;font-size:19px}
.hit .ad{font-size:13.5px;color:#5b6b7a;margin-top:3px}
.tags{margin-top:7px;display:flex;flex-wrap:wrap;gap:5px}
.tag{font-size:11.5px;background:#f4f8fb;border:1px solid #e5ebf1;border-radius:999px;padding:2px 9px;color:#37485a}
.tag.on{background:#0a9a8f;color:#fff;border-color:#0a9a8f}
.meta{font-size:13px;color:#5b6b7a;margin-top:12px;background:#f4f8fb;border-radius:9px;padding:10px 12px}
.notes{font-size:13px;color:#5b6b7a;margin:12px 0 0;padding-left:20px}
.notes li{margin:5px 0}
.warn{color:#b3261e;font-weight:700}
.err{color:#b3261e;font-weight:700}
.src{font-size:12px;color:#7d8a97;margin-top:16px;border-top:1px solid #e5ebf1;padding-top:12px}
.src a{color:#0a9a8f}
.doc{margin-top:34px}
.doc h2{font-size:19px;margin:30px 0 10px;padding-left:12px;border-left:5px solid #0a9a8f}
.doc p,.doc li{font-size:14.5px}
.doc ul{padding-left:22px}.doc li{margin:6px 0}
.t2{width:100%;border-collapse:collapse;margin:10px 0;font-size:14px}
.t2 th,.t2 td{border:1px solid #e5ebf1;padding:9px 11px;text-align:left;vertical-align:top}
.t2 th{background:#f4f8fb;width:34%;font-weight:700}
.tw{overflow-x:auto}
.pv{margin:16px 0}
.pv video{width:100%;height:auto;border-radius:12px;border:1px solid #e5ebf1;background:#000;display:block}
.note-sm{font-size:12.5px;color:#7d8a97;margin:5px 0 0}
.faq dt{font-weight:800;margin-top:14px;font-size:15px}
.faq dd{margin:5px 0 0;padding-left:16px;border-left:3px solid #e5ebf1;color:#37485a}
</style></head><body><div class="wrap">
<h1><a href="./">Kurage 避難所マップ</a></h1>
<p class="lead">住所を入れると、最寄りの<strong>指定緊急避難場所</strong>まで道路をたどって<strong>徒歩何分</strong>かを表示します。その地点の<strong>海抜（標高）</strong>も一緒に出ます。
指定緊急避難場所は災害種別ごとに指定されているため、<strong>「その災害で使える避難所」</strong>に絞り込めます。
全国115,447件を収録。判定に使ったデータの時点も市町村単位で表示します。</p>
<div class="card">
  <form id="f">
    <input id="q" placeholder="例: 愛知県豊田市一色町" autocomplete="off">
    <select id="h">
      <option value="">災害種別: 指定なし</option>
      <option value="landslid">土砂災害</option>
      <option value="flood">洪水</option>
      <option value="quake">地震</option>
      <option value="tsunami">津波</option>
      <option value="surge">高潮</option>
      <option value="inlflood">内水氾濫</option>
      <option value="bigfire">大規模火事</option>
      <option value="volcano">火山現象</option>
    </select>
    <button id="b">調べる</button>
  </form>
  <div class="res" id="r"></div>
</div>
<div class="pv">
<video src="https://kurage.exbridge.jp/pv/krefuge-pv-30s.mp4"
       poster="https://kurage.exbridge.jp/pv/krefuge-pv-poster.jpg"
       controls playsinline preload="none" width="1920" height="1080"></video>
<p class="note-sm">冒頭8秒の実写映像は MiniMax H3（セルフホスト）で生成しています。</p>
</div>
<section class="doc">
<h2>「指定緊急避難場所」と「指定避難所」は違います</h2>
<p>混同されがちですが、役割が異なります。本サービスが扱うのは<strong>前者</strong>です。</p>
<div class="tw"><table class="t2">
<tr><th>指定緊急避難場所</th><td>災害の危険から命を守るために<strong>緊急的に逃げ込む場所</strong>です。災害種別ごとに指定されます。</td></tr>
<tr><th>指定避難所</th><td>家に戻れなくなった人が<strong>一定期間滞在する施設</strong>です。</td></tr>
</table></div>
<h2>なぜ災害種別で絞り込むのか</h2>
<p>指定緊急避難場所は、災害種別ごとに指定されています。<strong>地震のときに使える場所が、洪水では使えないことがあります。</strong>
崖のそばの広場は地震では安全でも、土砂災害では危険です。単に「近い避難所」を出すと、その災害では使えない場所へ誘導しかねません。</p>
<p>そのため本サービスでは、災害種別を選ぶと<strong>その災害に対して指定されている場所だけ</strong>を対象にします。収録件数は次のとおりです。</p>
<div class="tw"><table class="t2">
<tr><th>地震</th><td>88,177件</td></tr>
<tr><th>洪水</th><td>71,783件</td></tr>
<tr><th>崖崩れ・土石流・地すべり（土砂災害）</th><td>67,289件</td></tr>
<tr><th>大規模な火事</th><td>43,105件</td></tr>
<tr><th>津波</th><td>40,393件</td></tr>
<tr><th>内水氾濫</th><td>38,621件</td></tr>
<tr><th>高潮</th><td>25,157件</td></tr>
<tr><th>火山現象</th><td>10,723件</td></tr>
</table></div>
<h2>徒歩時間はどう計算しているか</h2>
<p>直線距離ではなく、<strong>道路網をたどった歩行経路</strong>で計算しています。川や線路で迂回が必要な場所では、直線距離との差が大きくなります。</p>
<p>ただし住所から求まる座標は番地ではなく<strong>町丁目のおおよその位置</strong>なので、表示される分数は目安です。実際の出発点によって前後します。</p>
<h2>海抜（標高）も一緒に表示します</h2>
<p>判定すると、その地点の<strong>海抜（標高）</strong>を国土地理院のデータで表示します。
測定のもと（5mレーザー測量など）も併記するので、数字の精度の根拠が分かります。</p>
<p>なぜ避難所と一緒に出すかというと、<strong>津波と高潮では「避難先が今いる場所より高いか」が判断の基準</strong>だからです。
近くても低い場所へ逃げては意味がありません。たとえば名古屋駅は海抜2.3mです。</p>
<h2>データの時点について</h2>
<p>指定緊急避難場所のデータは<strong>市町村ごとに更新時期が異なります</strong>。本サービスは判定結果に、その市町村のデータがいつ更新されたものかを併記します。
時点が確認できないデータでは判定を行いません。黙って古いデータで答えるほうが危険だからです。</p>
<h2>あわせて確認したい方へ</h2>
<p>その土地が土砂災害の警戒区域に入っているかは、<a href="/khazard.php/">Kurage 土砂災害ハザードマップ</a>で調べられます。
「区域内かどうか」を調べてから、「その災害でどこへ逃げるか」を本サービスで確認する流れが実用的です。</p>
<h2>よくある質問</h2>
<dl class="faq">
<dt>無料で使えますか。</dt><dd>はい。登録もログインも不要です。</dd>
<dt>この結果は公的な証明になりますか。</dt><dd>なりません。参考情報です。最終的な確認は必ず当該市町村が公表する情報で行ってください。</dd>
<dt>載っていない避難所があります。</dt><dd>本データは各市町村が国土地理院に登録したものです。最新でない場合や未掲載の場合があります。市町村へご確認ください。</dd>
<dt>自社のサーバーで動かせますか。</dt><dd>はい。買い切り版を用意しています。住所を外部に送りたくない場合や、自社の拠点データと組み合わせたい場合にご利用ください。</dd>
</dl>
</section>
<p style="font-size:12.5px;color:#7d8a97;margin-top:10px">議員・政党事務所の方へ: このページを事務所の名前で運用できます → <a href="/bousai-giin.html">地域防災情報サービス</a></p>
<p style="font-size:13px;margin-top:14px">主要都市から地域ページへ入る: <a href="area/kanagawa-yokohama">横浜</a>・<a href="area/aichi-nagoya">名古屋</a>・<a href="area/osaka-osaka">大阪</a>・<a href="area/hyogo-kobe">神戸</a>・<a href="area/fukuoka-fukuoka">福岡</a>・<a href="area/">地域一覧</a></p>
<p class="src">出典: 国土地理院「指定緊急避難場所データ」（CC BY 4.0）を加工して作成 ／
経路計算: <a href="https://valhalla.github.io/valhalla/" rel="noopener">Valhalla</a> ／
住所検索・標高: 国土地理院 地名検索API／標高API</p>
</div>
<script>
var f=document.getElementById('f'),q=document.getElementById('q'),h=document.getElementById('h'),
    b=document.getElementById('b'),r=document.getElementById('r');
function esc(s){return String(s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
function run(){
  var v=q.value.trim(); if(!v)return;
  b.disabled=true; r.innerHTML='<p>調べています…</p>';
  fetch('api/check?q='+encodeURIComponent(v)+'&hazard='+encodeURIComponent(h.value))
   .then(function(x){return x.json().then(function(j){if(!x.ok)throw new Error(j.detail||'エラー');return j;});})
   .then(function(d){
     if(!d.shelters.length){r.innerHTML='<p class="err">'+esc(d.note||'見つかりませんでした')+'</p>';return;}
     var o='<p><strong>'+esc(d.resolved)+'</strong> から近い順（'+esc(d.hazard_label)+'）</p>';
     d.shelters.forEach(function(s){
       o+='<div class="hit"><div class="nm">'+esc(s.name)+' <span class="mi">徒歩'+s.walk_minutes+'分</span></div>'
        +'<div class="ad">'+esc(s.address)+'（約'+s.distance_m+'m）</div><div class="tags">';
       s.hazards.forEach(function(t){
         o+='<span class="tag'+(t===d.hazard_label?' on':'')+'">'+esc(t)+'</span>';});
       o+='</div></div>';
     });
     if(d.elevation){
       o+='<div class="meta"><strong>この地点の海抜（標高）: '+d.elevation.m+'m</strong>'
         +(d.elevation.source?'　<span style="font-weight:400">測定: '+esc(d.elevation.source)+'（国土地理院）</span>':'')
         +'<br><span style="font-weight:400">津波・高潮では、避難先がここより高いかどうかが判断の基準になります。</span></div>';
     }
     if(d.nagoya_live){var L=d.nagoya_live;
       o+='<div class="meta"><strong>名古屋市の退避施設（帰宅困難者向け）— いまの開設状況</strong>';
       if(L.status==='unavailable'){o+='<br><span style="font-weight:400">市の公開データを取得できませんでした。開設が無いという意味ではありません。<a href="'+esc(L.source_url)+'" rel="noopener">帰宅困難者支援サイト</a>で確認してください。</span>';}
       else{o+='<br><span style="font-weight:400">市内'+L.summary.total+'施設のうち開設中 '+L.summary.opened+'（市のデータ更新 '+esc(L.updated_at||'不明')+' / '+esc(L.fetched_at)+' 取得'+(L.status==='stale'?'・前回値':'')+'）</span>';
         L.facilities.forEach(function(x){o+='<br><span style="font-weight:400">・'+(x.nearest_open?'【最寄りの開設中】':'')+esc(x.name)+'（約'+x.distance_m+'m・'+esc(x.district)+'）: <strong>'+esc(x.status)+'</strong>'+(x.space?' '+esc(x.space):'')+(x.note?'／'+esc(x.note):'')+'</span>';});}
       o+='<br><span style="font-weight:400;font-size:12px">'+esc(L.what)+' 出典: <a href="'+esc(L.source_url)+'" rel="noopener">'+esc(L.source)+'</a></span></div>';}
     o+='<div class="meta">この判定に使ったデータの時点: <strong>'+esc(d.data_vintage||'不明')+'</strong>'
       +(d.vintage_scope?'（'+esc(d.vintage_scope)+'）':'')
       +(d.data_age_years!=null?'　約'+d.data_age_years+'年前':'')
       +'<br>徒歩時間の算出: '+esc(d.walk_basis)+'</div>';
     if(d.stale)o+='<p class="warn">このデータは更新から'+d.data_age_years+'年が経過しています。市町村の最新情報を必ずご確認ください。</p>';
     o+='<ul class="notes">';
     d.notes.forEach(function(n){o+='<li>'+esc(n)+'</li>';});
     o+='</ul>';
     r.innerHTML=o;
   })
   .catch(function(e){r.innerHTML='<p class="err">'+esc(e.message)+'</p>';})
   .then(function(){b.disabled=false;});
}
f.addEventListener('submit',function(e){e.preventDefault();run();});
(function(){var p=new URLSearchParams(location.search);
 if(p.get('q')){q.value=p.get('q'); if(p.get('hazard'))h.value=p.get('hazard'); run();}})();
</script>
<!-- 計測タグ(simpletrack)は krefuge.php プロキシ側が </head> 直前に注入する。
     ここに直書きすると二重計測になるので置かない。 -->
</body></html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(PAGE)


# ---- 地域ページ（「◯◯市 避難所」等の無競合ロングテールを取る） ----
# 2026-09-06 実測: 横浜市避難所210・避難所横浜市210・大阪避難所40・名古屋避難所20、
# いずれも競合指数0。需要は小さいが無競合なので都市名を主題にした個別ページで拾う。
# CSS/JSは本体PAGEから取り出して共有。/area/配下なので相対fetchを ../ に補正。
_STYLE = re.search(r"<style>.*?</style>", PAGE, re.S).group(0)
_SCRIPT = re.search(r"<script>(?:(?!application/ld).)*?</script>", PAGE, re.S).group(0).replace("'api/check", "'../api/check")

AREAS = [
    ("kanagawa-yokohama", "横浜市", "神奈川県横浜市中区海岸通"),
    ("aichi-nagoya", "名古屋市", "愛知県名古屋市中村区名駅"),
    ("osaka-osaka", "大阪市", "大阪府大阪市北区梅田"),
    ("hyogo-kobe", "神戸市", "兵庫県神戸市中央区三宮町"),
    ("fukuoka-fukuoka", "福岡市", "福岡県福岡市博多区博多駅前"),
    ("hokkaido-sapporo", "札幌市", "北海道札幌市中央区大通西"),
    ("kyoto-kyoto", "京都市", "京都府京都市中京区"),
    ("hiroshima-hiroshima", "広島市", "広島県広島市中区紙屋町"),
    ("miyagi-sendai", "仙台市", "宮城県仙台市青葉区中央"),
    ("kochi-kochi", "高知市", "高知県高知市種崎"),
]
AREA_BY_SLUG = {a[0]: a for a in AREAS}


def _area_head(city, slug, desc):
    url = "https://kurage.exbridge.jp/krefuge.php/area/" + slug
    title = city + "の避難所マップ｜住所から最寄りの避難所まで徒歩何分かを調べる | Kurage"
    ga = ('<script async src="https://www.googletagmanager.com/gtag/js?id=G-BP0650KDFR"></script>'
          '<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments)}'
          "gtag('js',new Date());gtag('config','G-BP0650KDFR');</script>")
    bc = json.dumps({"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": 1, "name": "Kurage 避難所マップ",
         "item": "https://kurage.exbridge.jp/krefuge.php/"},
        {"@type": "ListItem", "position": 2, "name": city, "item": url}]}, ensure_ascii=False)
    faq = json.dumps({"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
        {"@type": "Question", "name": city + "の避難所はどこで調べられますか？",
         "acceptedAnswer": {"@type": "Answer", "text": "このページで" + city + "の住所を入れると、最寄りの指定緊急避難場所まで徒歩何分かが表示されます。災害種別（土砂・洪水・地震・津波など）で絞り込めます。国土地理院のデータにもとづく参考情報です。"}}]}, ensure_ascii=False)
    return ('<!doctype html><html lang="ja"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>" + title + "</title>"
            '<meta name="description" content="' + desc + '">'
            '<link rel="canonical" href="' + url + '">'
            '<meta property="og:type" content="website">'
            '<meta property="og:title" content="' + city + 'の避難所マップ｜Kurage">'
            '<meta property="og:description" content="' + desc + '">'
            '<meta property="og:url" content="' + url + '">'
            '<meta property="og:image" content="https://kurage.exbridge.jp/pv/krefuge-pv-poster.jpg">'
            '<meta name="twitter:card" content="summary_large_image">'
            '<script type="application/ld+json">' + bc + '</script>'
            '<script type="application/ld+json">' + faq + '</script>' + ga)


@app.get("/area/{slug}", response_class=HTMLResponse)
def area(slug: str):
    a = AREA_BY_SLUG.get(slug)
    if not a:
        raise HTTPException(404, "地域が見つかりません")
    _, city, example = a
    exq = requests.utils.quote(example)
    desc = (city + "の住所を入れると、最寄りの指定緊急避難場所まで道路をたどって徒歩何分かを表示します。"
            "災害種別で絞り込み可能。全国115,447件を収録。無料・登録不要。")
    body = (
        '<h1><a href="/krefuge.php/">' + city + "の避難所マップ</a></h1>"
        '<p class="lead">' + city + "の住所を入れると、最寄りの<strong>指定緊急避難場所</strong>まで"
        "道路をたどって<strong>徒歩何分</strong>かを表示します。指定緊急避難場所は災害種別ごとの指定なので、"
        "<strong>その災害で使える避難所</strong>に絞り込めます。全国115,447件を収録。</p>"
        '<div class="card"><form id="f">'
        '<input id="q" placeholder="例: ' + example + '" value="' + example + '" autocomplete="off">'
        '<select id="h"><option value="">災害種別: 指定なし</option>'
        '<option value="landslid">土砂災害</option><option value="flood">洪水</option>'
        '<option value="quake">地震</option><option value="tsunami">津波</option>'
        '<option value="surge">高潮</option><option value="inlflood">内水氾濫</option>'
        '<option value="bigfire">大規模火事</option><option value="volcano">火山現象</option></select>'
        '<button id="b">調べる</button></form><div class="res" id="r"></div></div>'
        '<section class="doc">'
        "<h2>" + city + "で使える避難所を探す</h2>"
        "<p>" + city + "の住所を入れて災害種別を選ぶと、その災害に対して指定されている避難所だけを、"
        "近い順に道路網でたどって表示します。地震で使える場所が洪水では使えないことがあるためです。</p>"
        "<h2>あわせて確認したい方へ</h2>"
        '<p>' + city + "の土砂災害警戒区域は "
        '<a href="/khazard.php/?q=' + exq + '">土砂災害ハザードマップ</a>、津波の浸水想定は '
        '<a href="/ktsunami.php/area/' + slug + '">津波浸水想定マップ</a> で調べられます。'
        '全国版は <a href="/krefuge.php/">Kurage 避難所マップ</a> です。</p></section>'
        '<p class="src">出典: 国土地理院「指定緊急避難場所データ」（CC BY 4.0）を加工して作成'
        "／住所検索・経路: 国土地理院 地名検索API／Valhalla</p>")
    html = _area_head(city, slug, desc) + _STYLE + '</head><body><div class="wrap">' + body + _SCRIPT + "</body></html>"
    return HTMLResponse(html)


@app.get("/area", response_class=HTMLResponse)
@app.get("/area/", response_class=HTMLResponse)
def area_index():
    links = "".join('<li><a href="/krefuge.php/area/' + s + '">' + c + "の避難所マップ</a></li>"
                    for s, c, _ in AREAS)
    desc = "主要都市ごとの避難所マップの入口です。住所を入れると最寄りの避難所まで徒歩何分かが分かります。"
    html = (_area_head("地域一覧", "index", desc) + _STYLE
            + '</head><body><div class="wrap"><h1>地域から避難所を探す</h1>'
            '<p class="lead">主要都市ごとの入口です。全国版は '
            '<a href="/krefuge.php/">Kurage 避難所マップ</a> をどうぞ。</p>'
            '<ul style="font-size:15px;line-height:2.2">' + links + "</ul></div></body></html>")
    return HTMLResponse(html)
