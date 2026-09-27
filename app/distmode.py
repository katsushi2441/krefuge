# -*- coding: utf-8 -*-
"""配布モード。買った会社が自分のドメインで公開するときのための書き換え（当社の公開先では何もしない）。

環境変数 KURAGE_PUBLIC_ORIGIN（例: https://bousai.example.jp）を設定すると、
HTML・XML・テキストの応答について次を行う。**設定しなければ一切触らない**（当社の kurage.exbridge.jp 用）。

- canonical・og:url・サイトマップ・地域ページ・llms.txt の https://kurage.exbridge.jp/ を、その origin に置き換える
  （/pv/ と /images/ の画像・動画は当社の公開物なので置き換えず、PV のブロックごと下で消す）
- <!--kurage-only--> … <!--/kurage-only--> で囲んだ当社の販売・PV・自社サイトへの誘導を消す
- llms.txt の「## 買い切り版」「## オンプレミス版」の節を消す
- 運営者の表記（株式会社エクスブリッジ・EXBRIDGE, INC.・https://exbridge.jp/）を
  KURAGE_OPERATOR / KURAGE_BRAND_SUB / KURAGE_OPERATOR_URL に置き換える（未設定なら空にする）
"""
import os
import re

from starlette.responses import Response

ORIGIN = os.environ.get('KURAGE_PUBLIC_ORIGIN', '').rstrip('/')
OPERATOR = os.environ.get('KURAGE_OPERATOR', '')
OPERATOR_URL = os.environ.get('KURAGE_OPERATOR_URL', '')
BRAND_SUB = os.environ.get('KURAGE_BRAND_SUB', '')

_ONLY = re.compile(r'<!--kurage-only-->.*?<!--/kurage-only-->', re.S)
_SALES = re.compile(r'^## (?:買い切り版|オンプレミス版)\n(?:.+\n)*\n?', re.M)
_OWN = re.compile(r'https://kurage\.exbridge\.jp/(?!pv/|images/)')
_TYPES = ('text/html', 'xml', 'text/plain', 'application/ld+json')


def rewrite(text: str) -> str:
    text = _ONLY.sub('', text)
    text = _SALES.sub('', text)
    text = _OWN.sub(ORIGIN + '/', text)
    text = text.replace('EXBRIDGE, INC.', BRAND_SUB)
    text = text.replace('"https://exbridge.jp/"', '"%s"' % OPERATOR_URL).replace('https://exbridge.jp/', OPERATOR_URL)
    text = text.replace('株式会社エクスブリッジ', OPERATOR)
    return text


def install(app):
    if not ORIGIN:
        return

    @app.middleware('http')
    async def _distmode(request, call_next):
        resp = await call_next(request)
        ct = resp.headers.get('content-type', '')
        if not any(t in ct for t in _TYPES):
            return resp
        body = b''.join([chunk async for chunk in resp.body_iterator])
        try:
            text = body.decode('utf-8')
        except UnicodeDecodeError:
            return Response(body, status_code=resp.status_code, headers=dict(resp.headers))
        headers = {k: v for k, v in resp.headers.items() if k.lower() != 'content-length'}
        return Response(rewrite(text), status_code=resp.status_code, headers=headers)
