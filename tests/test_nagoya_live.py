# -*- coding: utf-8 -*-
"""名古屋市 退避施設 Feature Service の解析を 2026-09-08 の実データ（112件・開設14件）で固定する。 実行: .venv/bin/python -m pytest -q tests"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import nagoya_live  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'nagoya_taihi_20260908.json')


def _data():
    j = json.load(open(FIX, encoding='utf-8'))
    return nagoya_live.parse(j['meta'], j['query'])


def test_parse_and_summary():
    d = _data()
    assert d['updated_at'] and d['updated_at'].startswith('2026-09-08')
    s = nagoya_live.summary(d)
    assert s['total'] >= 100 and s['opened'] == 14


def test_nearest_from_nagoya_station():
    d = _data()
    near = nagoya_live.nearest(d, 35.1709, 136.8815, 3)
    assert len(near) == 3 and near[0]['distance_m'] < 600
    assert all('status' in x and x['status'] for x in near)


def test_unavailable_shape():
    assert nagoya_live.summary({'facilities': []}) == {'total': 0, 'opened': 0}
    assert nagoya_live.nearest({'facilities': []}, 35.0, 136.0) == []
