#!/usr/bin/env python3
"""「わがまちハザードマップ」から市区町村の公式ハザードマップのリンクを取り込む。

国土交通省 ハザードマップポータルサイトの「わがまちハザードマップ」は、
各市区町村が作るハザードマップのページへのリンク集。市区町村コード・担当課・
住所・電話・URL がタブ区切りで置かれている（画面のJSが読んでいる実体）。

  https://disaportal.gsi.go.jp/hazardmap/uty/hminfo/<災害コード>.txt

災害コードは公式の説明が無いので実測で確定させた（2026-09-14）:
  1=洪水 2=内水 3=高潮 4=津波 5=土砂災害 6=火山 7=ため池
  根拠: 4は鹿児島市がtsunami.html、6は鹿児島市がsakurajima.htmlで火山のある自治体だけ、
        2は鹿児島市が水道局下水道部・雨水整備室、3は沿岸自治体のみ、7は農政部。
        1と5の切り分けは、小笠原村・天城町（島で急斜面・河川が小さい）が5のみ、
        新篠津村・南幌町（石狩川流域の平野）が1のみ、であることから 5=土砂災害。

利用条件: ハザードマップポータルサイトのコンテンツは公共データ利用規約（PDL1.0）。
出典表示で商用利用・加工が可。ただし**リンク先のハザードマップの著作権は各市町村**にあり、
リンク先が最新版でないことがある（規約にその旨の明示あり）。画面にもそう書く。

  cd /home/kojima/work/krefuge && /usr/bin/python3 scripts/fetch_wagamachi.py
"""
import os, sys, json, subprocess, urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = "https://disaportal.gsi.go.jp/hazardmap/uty/hminfo/"
SOURCE_PAGE = "https://disaportal.gsi.go.jp/hazardmap/index.html"
KINDS = {1: "洪水", 2: "内水", 3: "高潮", 4: "津波", 5: "土砂災害", 6: "火山", 7: "ため池"}
UA = {"User-Agent": "Mozilla/5.0 (compatible; kurage-bousai/1.0; +https://kurage.exbridge.jp/)"}


def fetch(code: int):
    req = urllib.request.Request(BASE + f"{code}.txt", headers=UA)
    with urllib.request.urlopen(req, timeout=90) as r:
        raw = r.read()
    for enc in ("utf-8", "cp932", "euc_jp"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def parse(text):
    """市区町村コード -> {pref, muni, dept, address, tel, url}"""
    out = {}
    for line in text.splitlines():
        f = [c.strip() for c in line.split("\t")]
        if len(f) < 7 or not f[0].isdigit() or len(f[0]) != 5:
            continue
        url = f[6].strip()
        if not url.startswith("http"):
            continue
        out[f[0]] = {"pref": f[1], "muni": f[2], "dept": f[3], "address": f[4],
                     "tel": f[5], "url": url}
    return out


def head(url):
    """生存確認。ポータル側のリンクにも404が混じっている（実測で1割弱）。
    死んだリンクへ利用者を送らないよう、状態を持たせて画面で出し分ける。"""
    try:
        r = subprocess.run(["curl", "-sL", "-o", "/dev/null", "-w", "%{http_code}",
                            "--max-time", "15", "-A", "Mozilla/5.0", url],
                           capture_output=True, text=True, timeout=25)
        return r.stdout.strip() or "000"
    except Exception:  # noqa: BLE001
        return "000"


def verify(data):
    urls = sorted({it["url"] for kinds in data.values() for it in kinds.values()})
    print(f"  リンク生存確認 {len(urls):,} 本 …")
    with ThreadPoolExecutor(max_workers=16) as ex:
        status = dict(zip(urls, ex.map(head, urls)))
    # 一時的な 5xx・4xx（混雑・メンテ）で生きているリンクを消さないよう、落とす候補は間をおいてもう一度だけ確かめる
    bad = [u for u, c in status.items() if (c.startswith("4") and c != "403") or c.startswith("5")]
    if bad:
        import time
        time.sleep(30)
        with ThreadPoolExecutor(max_workers=4) as ex:
            status.update(zip(bad, ex.map(head, bad)))
    dead = 0
    for kinds in data.values():
        for it in kinds.values():
            code = status.get(it["url"], "000")
            it["http"] = code
            # 403/000 は bot 拒否や接続制限のことがあるので「生きている扱い」にする。
            # 明確な 4xx/5xx だけ落とす。
            it["ok"] = not (code.startswith("4") and code != "403") and not code.startswith("5")
            if not it["ok"]:
                dead += 1
    print(f"  → 表示しないリンク {dead:,} 本")
    return data


def build(codes, out_path, do_verify=True):
    data, meta = {}, {}
    for c in codes:
        rows = parse(fetch(c))
        meta[KINDS[c]] = len(rows)
        for mc, row in rows.items():
            data.setdefault(mc, {})[KINDS[c]] = row
    if do_verify:
        data = verify(data)
    payload = {"_source": "わがまちハザードマップ（ハザードマップポータルサイト・国土交通省）",
               "_source_url": SOURCE_PAGE,
               "_license": "公共データ利用規約（第1.0版）PDL1.0",
               "_note": "リンク先のハザードマップの著作権は各市町村。最新版でない場合がある。",
               "_fetched": __import__("datetime").date.today().isoformat(),
               "_counts": meta, "muni": data}
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    json.dump(payload, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"{out_path}: 市区町村 {len(data):,} / 内訳 {meta}")


if __name__ == "__main__":
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # krefuge が使う災害種別
    build([1, 4, 5], os.path.join(root, "data", "wagamachi.json"),
          do_verify="--no-verify" not in sys.argv)
