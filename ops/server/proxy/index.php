<?php
// Phronesis-Campus: PHP proxy from Apache (HTTPS) to the local API on 127.0.0.1 (the host has no mod_proxy). Pattern of the sibling
// products, reviewed for this application:
//  - every method with its body is forwarded (the API uses PUT for drafts, POST for commands);
//  - request headers are forwarded except Host (so X-CSRF-Token, Idempotency-Key, Cookie, Content-Type pass through);
//  - response headers are forwarded by allowlist, Set-Cookie with header(..., false) so several cookies survive;
//  - redirects are passed through, never followed here (login redirects to /campus; the browser follows);
//  - the proxy adds X-Forwarded-Proto/For; the API trusts them only from 127.0.0.1.
$port = 8060;
$target = 'http://127.0.0.1:' . $port . $_SERVER['REQUEST_URI'];
$method = $_SERVER['REQUEST_METHOD'];

$ch = curl_init($target);
curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
curl_setopt($ch, CURLOPT_HEADER, true);
curl_setopt($ch, CURLOPT_FOLLOWLOCATION, false);
curl_setopt($ch, CURLOPT_CONNECTTIMEOUT, 5);
curl_setopt($ch, CURLOPT_TIMEOUT, 120);
curl_setopt($ch, CURLOPT_CUSTOMREQUEST, $method);
if ($method !== 'GET' && $method !== 'HEAD') {
    curl_setopt($ch, CURLOPT_POSTFIELDS, file_get_contents('php://input'));
}
if ($method === 'HEAD') {
    curl_setopt($ch, CURLOPT_NOBODY, true);
}
$headers = [];
foreach (getallheaders() as $k => $v) {
    $lk = strtolower($k);
    if ($lk === 'host' || $lk === 'content-length' || $lk === 'accept-encoding' || $lk === 'connection') continue;
    $headers[] = "$k: $v";
}
$headers[] = 'X-Forwarded-Proto: https';
$headers[] = 'X-Forwarded-For: ' . $_SERVER['REMOTE_ADDR'];
$headers[] = 'X-Forwarded-Host: ' . ($_SERVER['HTTP_HOST'] ?? 'university.kaspair.ch');
curl_setopt($ch, CURLOPT_HTTPHEADER, $headers);
$response = curl_exec($ch);
if ($response === false) {
    http_response_code(502);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode(['code' => 'UPSTREAM_UNAVAILABLE', 'message' => 'Campus-API nicht erreichbar', 'retryable' => true]);
    curl_close($ch);
    exit;
}
$info = curl_getinfo($ch);
curl_close($ch);
$header_size = $info['header_size'];
$resp_headers = substr($response, 0, $header_size);
$body = substr($response, $header_size);
http_response_code($info['http_code']);
$allow = ['content-type', 'set-cookie', 'location', 'x-trace-id', 'x-campus-capability-level', 'cache-control', 'content-disposition', 'etag', 'last-modified', 'vary'];
foreach (explode("\r\n", $resp_headers) as $h) {
    $pos = strpos($h, ':');
    if ($pos === false) continue;
    $name = strtolower(trim(substr($h, 0, $pos)));
    if (in_array($name, $allow, true)) {
        header($h, $name !== 'set-cookie');
    }
}
header('X-Content-Type-Options: nosniff');
header('Referrer-Policy: same-origin');
echo $body;
