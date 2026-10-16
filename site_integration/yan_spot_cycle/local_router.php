<?php
// Real local HTTP dispatcher. Not installed in the website overlay.
if (PHP_SAPI !== 'cli-server' || getenv('BRIDGE_LOCAL_SINGLE_USER') !== '1' ||
    getenv('VERCEL_ENV') || $_SERVER['REMOTE_ADDR'] !== '127.0.0.1') {
    http_response_code(403); exit;
}
$frontend = getenv('BRIDGE_SINGLE_USER_FRONTEND_ORIGIN');
$backend = getenv('BRIDGE_SINGLE_USER_BACKEND_ORIGIN');
foreach (array($frontend, $backend) as $origin) {
    $parsed = parse_url($origin);
    if (!$parsed || $parsed['scheme'] !== 'http' || $parsed['host'] !== '127.0.0.1' ||
        !isset($parsed['port']) || $parsed['port'] < 1024 || $parsed['port'] > 65535 ||
        isset($parsed['user']) || isset($parsed['pass']) || isset($parsed['path']) ||
        isset($parsed['query']) || isset($parsed['fragment'])) {
        http_response_code(503); exit;
    }
}
if ($_SERVER['HTTP_HOST'] !== parse_url($frontend, PHP_URL_HOST).':'.parse_url($frontend, PHP_URL_PORT) ||
    (isset($_SERVER['HTTP_ORIGIN']) && $_SERVER['HTTP_ORIGIN'] !== $frontend) ||
    isset($_SERVER['HTTP_FORWARDED']) || isset($_SERVER['HTTP_X_FORWARDED_FOR']) ||
    isset($_SERVER['HTTP_X_FORWARDED_HOST'])) {
    http_response_code(403); exit;
}
$path = parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH);
if ($path !== '/index.php') $_GET['route'] = trim(preg_replace('~^/(ru|en|he)/~', '/', $path), '/');
$route = isset($_GET['route']) && is_string($_GET['route']) ? trim($_GET['route'], '/') : '';
if ($route !== 'practice/spot-cycle' && strpos($route, 'practice/spot-cycle/') !== 0) {
    http_response_code(404); exit;
}
$_SERVER['SCRIPT_NAME'] = '/index.php';
$GLOBALS['bridgeSpotBackendDispatch'] = function ($request) use ($backend) {
    if (strpos($request['path'], '/school/lessons/spot-cycle/') !== 0) {
        throw new RuntimeException('target rejected');
    }
    $headers = array();
    foreach ($request['headers'] as $name => $value) {
        if (strpos($name.$value, "\r") !== false || strpos($name.$value, "\n") !== false) {
            throw new RuntimeException('header rejected');
        }
        $headers[] = $name.': '.$value;
    }
    $context = stream_context_create(array('http' => array('method' => $request['method'],
        'header' => implode("\r\n", $headers), 'content' => $request['body'],
        'ignore_errors' => true, 'follow_location' => 0, 'timeout' => 5)));
    $body = @file_get_contents($backend.$request['path'], false, $context);
    if ($body === false || !isset($http_response_header[0]) ||
        !preg_match('~^HTTP/\S+ ([0-9]{3})~', $http_response_header[0], $match)) {
        throw new RuntimeException('local backend unavailable');
    }
    $reply = array();
    foreach (array_slice($http_response_header, 1) as $line) {
        $colon = strpos($line, ':');
        if ($colon !== false) $reply[substr($line, 0, $colon)] = trim(substr($line, $colon + 1));
    }
    return array('status' => (int)$match[1], 'headers' => $reply, 'body' => $body);
};
require getenv('BRIDGE_YAN_CANDIDATE').'/index.php';
