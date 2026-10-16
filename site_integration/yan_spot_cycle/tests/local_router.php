<?php
// Opt-in local preview only. This file is never part of the site overlay.
if (PHP_SAPI !== 'cli-server' ||
    !in_array($_SERVER['REMOTE_ADDR'], array('127.0.0.1', '::1'), true) ||
    getenv('BRIDGE_YAN_LOCAL_PREVIEW') !== '1') {
    http_response_code(404); exit;
}
$path = parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH);
if ($path !== '/index.php') {
    $path = preg_replace('~^/(ru|en|he)/~', '/', $path);
    $_GET['route'] = trim($path, '/');
}
$route = isset($_GET['route']) && is_string($_GET['route']) ? trim($_GET['route'], '/') : '';
if ($route !== 'practice/spot-cycle' && strpos($route, 'practice/spot-cycle/') !== 0) {
    http_response_code(404); exit;
}
$_SERVER['SCRIPT_NAME'] = '/index.php';
$GLOBALS['bridgeSpotBackendDispatch'] = function ($request) {
    $pipes = array();
    $process = proc_open(array(getenv('BRIDGE_TEST_PYTHON'), __DIR__.'/backend_fixture.py'),
        array(0 => array('pipe', 'r'), 1 => array('pipe', 'w'), 2 => array('pipe', 'w')), $pipes);
    if (!is_resource($process)) throw new RuntimeException('fixture unavailable');
    fwrite($pipes[0], json_encode($request)); fclose($pipes[0]);
    $body = stream_get_contents($pipes[1]); fclose($pipes[1]);
    stream_get_contents($pipes[2]); fclose($pipes[2]);
    if (proc_close($process) !== 0) throw new RuntimeException('fixture unavailable');
    return json_decode($body, true);
};
require getenv('BRIDGE_YAN_CANDIDATE').'/index.php';
