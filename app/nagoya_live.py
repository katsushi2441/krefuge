# -*- coding: utf-8 -*-
"""名古屋市が公開している「退避施設（帰宅困難者向け）」の開設状況を、市の ArcGIS Feature Service から取る。

出典（2026-09-08 実測・公開・認証不要）:
  退避施設_公開用  https://services8.arcgis.com/0YBSVU4IGX12iZcu/arcgis/rest/services/退避施設_公開用/FeatureServer/0
    112件・属性: 地区/名称/緯度/経度/住所/スペース/備考/開設状況/利用者への連絡事項。
    2026-09-08 の大雨では「開設済み・空き」等に当日更新されていた（editingInfo.dataLastEditDate）。
  市の「帰宅困難者支援サイト」 https://www.city.nagoya.jp/bousaiportal/hazardmap/1013538.html のダッシュボードの元データ。

使わないもの:
  同じ組織の「★指定緊急避難場所・指定避難所（ArcGIS取り込み用20230803変更版）」には 開設状況 列があるが、
  最終更新が 2024-10-29 で、2026-09-08 の警戒レベル5発令中も 807件すべて「未開設」のままだった。
  更新されていないデータで「未開設」と出すのは危険なので、この製品では表示しない。

退避施設は「帰宅困難者が最大24時間滞在する場所」で、指定緊急避難場所（命を守るため逃げ込む場所）とは別物。画面でも混同させない。
取得できないときは「開設なし」と言わず「取得できない」と返す。
"""
import math
import threading
import time
import urllib.parse
from datetime import datetime

import requests

BASE = 'https://services8.arcgis.com/0YBSVU4IGX12iZcu/arcgis/rest/services/'
LAYER = BASE + urllib.parse.quote('退避施設_公開用') + '/FeatureServer/0'
SOURCE_NAME = '名古屋市 退避施設開設状況（帰宅困難者支援サイト）'
SOURCE_URL = 'https://www.city.nagoya.jp/bousaiportal/hazardmap/1013538.html'
UA = {'User-Agent': 'krefuge/1.0 (kurage.exbridge.jp; nagoya-live)'}
CACHE_SEC = 120
AREA = '名古屋市'
_lock = threading.Lock()
_cache = {'at': 0.0, 'data': None}


def _haversine(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def parse(meta: dict, query: dict) -> dict:
    """FeatureServer のメタ情報（editingInfo）と query 結果（features）を、施設の list にする。"""
    upd = (meta.get('editingInfo') or {}).get('dataLastEditDate')
    updated_at = datetime.fromtimestamp(upd / 1000).strftime('%Y-%m-%d %H:%M') if upd else None
    out = []
    for f in query.get('features', []):
        a = f.get('attributes', {})
        try:
            lat, lon = float(a.get('緯度')), float(a.get('経度'))
        except (TypeError, ValueError):
            continue
        if not (30 < lat < 40 and 130 < lon < 140):   # 緯度が欠けた行（実データに 35.0 のみ等がある）は距離計算に使わない
            continue
        out.append(dict(name=a.get('名称') or '', address=a.get('住所') or '', district=a.get('地区') or '', lat=lat, lon=lon,
                        space=a.get('スペース') or '', status=a.get('開設状況') or '不明', note=a.get('利用者への連絡事項') or a.get('備考') or ''))
    return dict(facilities=out, updated_at=updated_at, source=SOURCE_NAME, source_url=SOURCE_URL)


def fetch(force: bool = False) -> dict:
    with _lock:
        now = time.time()
        if not force and _cache['data'] and now - _cache['at'] < CACHE_SEC:
            return _cache['data']
        try:
            meta = requests.get(LAYER, params={'f': 'json'}, headers=UA, timeout=8).json()
            q = requests.get(LAYER + '/query', params={'where': '1=1', 'outFields': '*', 'returnGeometry': 'false', 'resultRecordCount': 500, 'f': 'json'},
                             headers=UA, timeout=10).json()
            if 'error' in q or 'error' in meta:
                raise RuntimeError('arcgis error')
            d = parse(meta, q)
            d.update(status='ok', fetched_at=datetime.now().strftime('%Y-%m-%d %H:%M'))
        except Exception as e:  # noqa: BLE001
            d = dict(status='unavailable', error=type(e).__name__, facilities=[], updated_at=None, source=SOURCE_NAME, source_url=SOURCE_URL,
                     fetched_at=datetime.now().strftime('%Y-%m-%d %H:%M'))
            if _cache['data'] and _cache['data'].get('status') == 'ok' and now - _cache['at'] < 1800:
                d = dict(_cache['data'], status='stale')
        _cache['at'], _cache['data'] = now, d
        return d


def nearest(data: dict, lat: float, lon: float, n: int = 3) -> list:
    rows = []
    for f in data.get('facilities', []):
        rows.append(dict(f, distance_m=round(_haversine(lat, lon, f['lat'], f['lon']))))
    rows.sort(key=lambda x: x['distance_m'])
    return rows[:n]


def summary(data: dict) -> dict:
    fs = data.get('facilities', [])
    opened = [f for f in fs if f['status'].startswith('開設')]
    return dict(total=len(fs), opened=len(opened))
