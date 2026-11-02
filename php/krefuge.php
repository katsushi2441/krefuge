<?php
// Kurage 避難所マップ (krefuge) — kurage.exbridge.jp 上の公開入口。
// 自宅サーバー :18378 への透過プロキシ。UIは相対パス(api/...)なので
// /krefuge.php/ (末尾スラッシュ) を起点に PATH_INFO で中継する。
// バックエンドURLは同ディレクトリの krefuge_config.php で定義する(リポジトリには含めない)
//   <?php define('KREFUGE_BACKEND', 'http://あなたのサーバー:18378');
$__cfg = __DIR__ . '/krefuge_config.php';
if (is_file($__cfg)) { require_once $__cfg; }
$BACKEND = defined('KREFUGE_BACKEND') ? KREFUGE_BACKEND : 'http://127.0.0.1:18378';

// /krefuge.php → /krefuge.php/ へ(相対URL解決のため)
if (!isset($_SERVER['PATH_INFO']) || $_SERVER['PATH_INFO'] === '') {
    if (substr($_SERVER['REQUEST_URI'], -1) !== '/' && strpos($_SERVER['REQUEST_URI'], '?') === false) {
        header('Location: /krefuge.php/', true, 302); exit;
    }
}
$path = isset($_SERVER['PATH_INFO']) ? $_SERVER['PATH_INFO'] : '/';
$qs = isset($_SERVER['QUERY_STRING']) && $_SERVER['QUERY_STRING'] !== '' ? '?' . $_SERVER['QUERY_STRING'] : '';

// 「エクスブリッジについて」の欄（正本は kurage_web/exbridge-about.html。文言・数字はそちらを直してから写す）。
// 当社のサイト（kurage.exbridge.jp・proto.exbridge.jp・exbridge.jp。www. 付きも可）で動いているときだけ出す。
// お客さまが自社に置いた版には、当社の会社概要を出さない。
function exb_about_html() {
    $host = strtolower(isset($_SERVER['HTTP_HOST']) ? (string)$_SERVER['HTTP_HOST'] : '');
    $host = (string)preg_replace('/:\d+$/', '', $host);
    if (strpos($host, 'www.') === 0) $host = substr($host, 4);
    if (!in_array($host, array('kurage.exbridge.jp', 'proto.exbridge.jp', 'exbridge.jp'), true)) return '';
    return <<<'EXB_ABOUT'
<section class="exb-about" style="max-width:1080px;margin:32px auto 0;padding:18px 16px 8px;border-top:1px solid #d6dde3;font-size:13.5px;line-height:1.75;color:#33414d;background:transparent;box-sizing:border-box;text-align:left">
  <h2 style="font-size:15px;line-height:1.5;margin:0 0 6px;color:#1f2d38;font-weight:700">エクスブリッジについて</h2>
  <p style="margin:0 0 8px">このページは、名古屋のシステム開発会社・株式会社エクスブリッジが運営しています。AIを使って中小企業の業務システムを作り、保守契約・月額のシステム利用料・要件定義なしで使える買い切りの業務システムを <a href="https://kappstore.exbridge.jp/#ref=exb-about-krefuge">Kurage App Store</a> で販売しています。</p>
  <dl style="display:grid;grid-template-columns:auto 1fr;gap:2px 12px;margin:0 0 8px">
    <dt style="font-weight:700">会社名</dt><dd style="margin:0">株式会社エクスブリッジ（EXBRIDGE, Inc.）</dd>
    <dt style="font-weight:700">所在地</dt><dd style="margin:0">愛知県名古屋市瑞穂区内浜町34-9 305</dd>
    <dt style="font-weight:700">設立</dt><dd style="margin:0">2004年4月</dd>
    <dt style="font-weight:700">資本金</dt><dd style="margin:0">1億250万円</dd>
    <dt style="font-weight:700">代表取締役</dt><dd style="margin:0">小嶋 篤</dd>
    <dt style="font-weight:700">法人番号</dt><dd style="margin:0">4180001056508</dd>
    <dt style="font-weight:700">事業</dt><dd style="margin:0">AIを使った業務システムの開発、Kurage App Store の運営、AI-IT顧問</dd>
  </dl>
  <p style="margin:0"><a href="https://exbridge.jp/profile.html#ref=exb-about-krefuge">会社概要</a>　<a href="https://exbridge.jp/vibeblog/2026-11-03-exbridge-yameta-3tsu-hoshu-getsugaku-youken.html#ref=exb-about-krefuge">エクスブリッジがやめた3つ</a>　<a href="https://exbridge.jp/ai-it-komon.html#ref=exb-about-krefuge">AI-IT顧問</a>　<a href="https://exbridge.jp/contact.php#ref=exb-about-krefuge">お問い合わせ</a></p>
</section>
EXB_ABOUT;
}

$ch = curl_init($BACKEND . $path . $qs);
$headers = array('X-Forwarded-Proto: https', 'X-Forwarded-Host: kurage.exbridge.jp');
if (!empty($_SERVER['HTTP_X_FORWARDED_FOR'])) { $headers[] = 'X-Forwarded-For: ' . $_SERVER['HTTP_X_FORWARDED_FOR']; }
elseif (!empty($_SERVER['REMOTE_ADDR'])) { $headers[] = 'X-Forwarded-For: ' . $_SERVER['REMOTE_ADDR']; }
curl_setopt_array($ch, array(
    CURLOPT_CUSTOMREQUEST => $_SERVER['REQUEST_METHOD'],
    CURLOPT_RETURNTRANSFER => true, CURLOPT_HEADER => true,
    CURLOPT_HTTPHEADER => $headers, CURLOPT_ENCODING => '',
    CURLOPT_TIMEOUT => 60, CURLOPT_FOLLOWLOCATION => false,
));
if ($_SERVER['REQUEST_METHOD'] !== 'GET') {
    curl_setopt($ch, CURLOPT_POSTFIELDS, file_get_contents('php://input'));
}
$res = curl_exec($ch);
if ($res === false) { http_response_code(502); header('Content-Type: text/plain; charset=utf-8');
    echo '避難所マップのバックエンドに接続できません'; exit; }
$status = curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
$hsize = curl_getinfo($ch, CURLINFO_HEADER_SIZE);
curl_close($ch);
http_response_code($status);
foreach (explode("\r\n", substr($res, 0, $hsize)) as $h) {
    if (stripos($h, 'Content-Type:') === 0 || stripos($h, 'Cache-Control:') === 0) { header($h); }
}
// 計測タグ(kurage系はsimpletrack)をHTMLにだけ差し込む
$body = substr($res, $hsize);
if (stripos((string)$status . implode('', headers_list()), 'text/html') !== false || strpos($body, '<!doctype html') === 0) {
    $tag = '<script>(function(){var s=document.createElement("script");s.src="https://kurage.exbridge.jp/simpletrack.php?url="+encodeURIComponent(location.href)+"&ref="+encodeURIComponent(document.referrer);s.async=true;document.head.appendChild(s)})();</script>';
    // 再販パートナー募集の枠（中身は kurage_web/partner-bar.js。kurage.exbridge.jp 以外では何も出さない）
    $tag .= '<script src="https://kurage.exbridge.jp/partner-bar.js" defer></script>';
    $body = str_replace('</head>', $tag . '</head>', $body);
    // 「エクスブリッジについて」の欄を本文の最後（</body> の直前）に。当社のサイト以外では空
    $__about = exb_about_html();
    $__p = strripos($body, '</body>');
    if ($__about !== '' && $__p !== false) { $body = substr($body, 0, $__p) . $__about . substr($body, $__p); }
}
echo $body;
