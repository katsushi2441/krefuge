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
}
echo $body;
