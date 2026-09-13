#!/usr/bin/env python3
"""A33 の住所文字列から市区町村を取り出す（地域ページの土台）。

A33（土砂災害警戒区域）には市区町村コードが無く、`address` に
「犬山市字裏山」「知多郡美浜町大字河和字花廻間」のような住所が入っているだけ。
全国地方公共団体コードの正典（krefuge の muni_vintage・1,747件）と突き合わせて市区町村を決める。

注意点（実測で分かった落とし穴）:
  ・正典の名前は郡を含む（「西諸県郡高原町」）。住所から郡を先に落とすと一致しなくなる。
    → **郡つきのまま先に照合し、外れたときだけ郡を落として再照合する**。
  ・`^.{1,4}郡` で機械的に削ると「大和郡山市」「鹿児島市郡山町」を壊す。
  ・「袖ヶ浦市」と正典「袖ケ浦市」のように ヶ/ケ が揺れる。
  ・合併前の旧市町村名（「吉良町」→ 現・西尾市）が残っている。正典に無いので拾えない。
    拾えなかった分は地域ページに載せず、件数だけ「市区町村不明」として開示する。
"""
import os, re, sqlite3, collections

KREFUGE_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "krefuge.db")
# `.{2,3}県` で書くと「西諸県郡高原町」の「西諸県」を県名として食う（実測）。47個を列挙する。
PREFS = ("北海道 青森県 岩手県 宮城県 秋田県 山形県 福島県 茨城県 栃木県 群馬県 埼玉県 千葉県 東京都 神奈川県 "
         "新潟県 富山県 石川県 福井県 山梨県 長野県 岐阜県 静岡県 愛知県 三重県 滋賀県 京都府 大阪府 兵庫県 "
         "奈良県 和歌山県 鳥取県 島根県 岡山県 広島県 山口県 徳島県 香川県 愛媛県 高知県 福岡県 佐賀県 長崎県 "
         "熊本県 大分県 宮崎県 鹿児島県 沖縄県").split()
_PREF_RE = re.compile("^(" + "|".join(sorted(PREFS, key=len, reverse=True)) + ")")
_GUN_RE = re.compile(r'^.{1,4}郡(?=.*?[町村])')


def _norm(s: str) -> str:
    # 異体字の揺れ: 袖ヶ浦/袖ケ浦・檮原/梼原・塩竃/塩竈 は同じ自治体（実測で出た分）
    return (s or "").translate(str.maketrans("ヶヵ之﨑檮竃內邊瀧栁", "ケカノ崎梼竈内辺滝柳")).strip()


def load_canon(path: str = KREFUGE_DB):
    """pref_code -> [(正規化名, 表示名, 団体コード)] を長い名前順で返す。"""
    pref, muni = {}, collections.defaultdict(list)
    con = sqlite3.connect(path)
    try:
        rows = con.execute("SELECT muni_code, muni_name FROM muni_vintage").fetchall()
    finally:
        con.close()
    for code, full in rows:
        m = _PREF_RE.match(full)
        if not m:
            continue
        pc, name = code[:2], full[m.end():]
        pref[pc] = m.group(1)
        muni[pc].append((_norm(name), name, code))
        # 郡を落とした形でも引けるようにしておく（住所側に郡が無い場合がある）
        bare = _GUN_RE.sub("", name)
        if bare != name:
            muni[pc].append((_norm(bare), name, code))
    for pc in muni:
        muni[pc].sort(key=lambda x: -len(x[0]))
    return pref, muni


def extract(pref_code: str, address: str, muni):
    """(表示名, 団体コード) か None。郡つきのまま先に照合し、外れたら郡を落として再照合。"""
    if not address:
        return None
    s = _norm(_PREF_RE.sub("", address))
    cands = muni.get(pref_code) or ()
    for name, disp, code in cands:
        if s.startswith(name):
            return disp, code
    s2 = _GUN_RE.sub("", s)
    if s2 != s:
        for name, disp, code in cands:
            if s2.startswith(name):
                return disp, code
    else:
        s2 = s
    # 「西臼杵郡高千穂」「東津軽郡今別」のように末尾の 町/村 が欠けている表記がある。
    # 完全一致のときだけ拾う（部分一致まで許すと別の自治体に化ける）。
    for name, disp, code in cands:
        if len(name) > 1 and name[-1] in "市区町村" and s2 == name[:-1]:
            return disp, code
    return None
