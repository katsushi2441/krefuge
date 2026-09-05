# 設置手順書 — Kurage 避難所マップ (krefuge)

PostGIS も Docker も要りません。Python と GDAL だけで動きます。

## 1. 動作条件

- Linux (Ubuntu 22.04 で動作確認)
- Python 3.10 以上
- GDAL (`ogr2ogr` コマンド) — `sudo apt install gdal-bin`
- ディスク 400MB 程度 (取り込み後は 30MB)
- メモリ 1GB 以上

徒歩時間を道路網で計算する場合のみ Valhalla が必要です(後述。無くても動きます)。

## 2. 展開と依存

```bash
unzip krefuge.zip -d /opt/krefuge
cd /opt/krefuge
python3 -m venv .venv
.venv/bin/pip install fastapi "uvicorn[standard]" requests
```

## 3. データの取得

国土地理院の全国 Shapefile を取得します。**再配布物には含まれていません**(最新版を使っていただくためです)。

```bash
mkdir -p data
cd data
curl -L -o hinanbasho.zip \
  "https://www.geospatial.jp/ckan/dataset/db74071f-cd0a-406d-ba3d-57c7a25d9925/resource/4db1e885-f688-4a92-aec0-f9ebf960c402/download/00.zip"
unzip -q hinanbasho.zip -d zenkoku

# 市町村別の公開日・最終更新日(判定結果に「いつ時点か」を出すのに使う)
curl -o publicHistoryListData.csv \
  "https://hinanmap.gsi.go.jp/hinanjocp/defaultFtpData/publicHistoryCSV/publicHistoryListData.csv"
cd ..
```

配布元: https://www.geospatial.jp/ckan/dataset/hinanbasho

## 4. 取り込み

`--vintage` にデータ時点(ダウンロードした版の日付)を必ず渡します。
**省略するとエラーになります。** 時点が分からないデータで判定させないための仕様です。

```bash
.venv/bin/python scripts/load_shelters.py --vintage 2026-06-12
```

Shapefile の `all.dbf` の更新日が目安です。

```bash
ogrinfo -so -al 'data/zenkoku/00_全国/all.shp' | grep DBF_DATE_LAST_UPDATE
```

成功すると件数と災害種別ごとの内訳が出ます。

```
  取り込み: 115,447件 / 都道府県 47 / 市町村時点 1747件
    洪水                      71,783
    崖崩れ・土石流・地すべり            67,289
    ...
```

## 5. 起動

```bash
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 18378
```

常駐させる場合は `systemd/krefuge.service` のパスを書き換えて設置します。

```bash
cp systemd/krefuge.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now krefuge
```

`Restart=always` を入れてあります。停止シグナルを受けても自動で復帰します。

## 6. 動作確認

```bash
curl 'http://127.0.0.1:18378/healthz'
# {"status":"ok","shelters":115447,"data_vintage":"2026-06-12"}

curl -G 'http://127.0.0.1:18378/api/check' \
  --data-urlencode 'q=愛知県豊田市一色町' --data 'hazard=landslid'
```

## 7. 徒歩時間を道路網で計算する (任意)

無くても動きます。その場合は直線距離×徒歩80m/分で計算し、
**結果にその旨が明記されます**(`walk_basis`)。黙って精度を落とすことはしません。

道路網で計算するには Valhalla を立て、環境変数で指すだけです。

```bash
KREFUGE_VALHALLA=http://127.0.0.1:8002 .venv/bin/uvicorn app.main:app --port 18378
```

Valhalla は日本の OSM データからタイルを作って起動します(公式イメージの手順どおり)。
`/sources_to_targets` を `costing: pedestrian` で使います。

## 8. レンタルサーバーから公開する (任意)

社内利用だけなら不要です。外部公開する場合、`php/krefuge.php` をレンタルサーバーに置き、
同じディレクトリに `krefuge_config.php` を作ります。

```php
<?php define('KREFUGE_BACKEND', 'http://あなたのサーバー:18378');
```

`https://example.com/krefuge.php/` (**末尾スラッシュ必須**) で開きます。

## 9. データの更新

指定緊急避難場所は随時更新されます。**年1回程度の再取得を推奨**します。
手順3〜4をもう一度実行するだけです(`--vintage` を新しい日付にしてください)。

## ご利用上の注意 (国土地理院の規約により必須)

第三者に情報提供する場合、以下が正確に伝わるようにしてください。**画面から外さないでください。**

- 本データは市町村長が指定した情報を各市町村に登録いただいたものです
- **最新でない場合や未掲載の場合があります。**最新かつ詳細の状況は必ず当該市町村にご確認ください
- 「指定緊急避難場所」と「指定避難所」は異なります
- 指定緊急避難場所は**災害種別ごとに指定**されています

出典表示「国土地理院『指定緊急避難場所データ』を加工して作成」は CC BY 4.0 の要件です。

## トラブル

| 症状 | 原因と対処 |
| --- | --- |
| 取り込みで `UnicodeDecodeError` | `ogr2ogr` に `-lco ENCODING=UTF-8` を付けていませんか。`.cpg` が UTF-8 なので二重変換で壊れます |
| `ogr2ogr: not found` | `sudo apt install gdal-bin` |
| 徒歩時間が実際より短い | Valhalla 未接続で直線計算に退避しています。結果の `walk_basis` を確認してください |
| 住所が見つからない | 国土地理院の地名検索APIの結果に依存します。町丁目まで入れると当たりやすくなります |
