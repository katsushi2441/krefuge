# -*- coding: utf-8 -*-
"""Kurage 防災AIチャット（kbousai）への誘導の帯。当社の公開先だけに出す（<!--kurage-only--> で囲むので配布モードでは消える）。"""
from urllib.parse import quote

URL = 'https://kurage.exbridge.jp/kbousai.php/'
_BOX = ('max-width:1000px;margin:12px auto 14px;background:#eef6fb;border:1px solid #bcd9ec;border-radius:10px;'
        'padding:10px 14px;font-size:14px;line-height:1.7;color:#16232e;box-sizing:border-box')


def bar(ref: str, q: str = '') -> str:
    """q（住所・市区町村名）があれば、防災AIチャットを開いた時点でその場所の「逃げた方がいい？」に答える。"""
    if q:
        href = f'{URL}?q={quote(q)}&msg={quote("逃げた方がいい？")}&ref={quote(ref)}'
        act = f'{q}で「いま逃げた方がいい？」を聞く →'
    else:
        href = f'{URL}?ref={quote(ref)}'
        act = 'Kurage 防災AIチャットで聞く →'
    return ('<!--kurage-only--><div style="' + _BOX + '"><b>台風・大雨のときは、いま逃げた方がいい？</b> '
            '住所か現在地で、警報・キキクル・台風の進路・川・津波・避難情報をまとめて答えます。 '
            f'<a href="{href}" style="font-weight:700;color:#0b5d8f">{act}</a></div><!--/kurage-only-->')
