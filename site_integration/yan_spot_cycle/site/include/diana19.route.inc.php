<?php
// Only the explicit local app registers this lesson. Never serves source files.
function bridge_diana_route($route) {
    $suffix = substr($route, strlen('practice/diana19'));
    $method = isset($_SERVER['REQUEST_METHOD']) ? $_SERVER['REQUEST_METHOD'] : 'GET';
    $get = $suffix === '' || $suffix === '/' || $suffix === '/catalog' ||
        preg_match('~^/lesson/diana19-q-00[123]$~D', $suffix) ||
        in_array($suffix, array('/assets/styles.css', '/assets/diana19.css', '/assets/diana19.js'), true);
    $post = preg_match('~^/answer/diana19-q-00[123]$~D', $suffix);
    if (!$get && !$post) { http_response_code(404); return; }
    if ($method !== ($post ? 'POST' : 'GET')) { http_response_code(405); return; }
    $body = $post ? file_get_contents(PHP_SAPI === 'cli' ? 'php://stdin' : 'php://input', false, null, 0, 20001) : '';
    if ($body === false || strlen($body) > 20000) { http_response_code(413); return; }
    $headers = array();
    foreach (array('HTTP_COOKIE'=>'Cookie', 'HTTP_ORIGIN'=>'Origin', 'HTTP_X_CSRF_TOKEN'=>'X-CSRF-Token',
        'CONTENT_TYPE'=>'Content-Type', 'HTTP_X_LEARNER_ID'=>'X-Learner-Id', 'HTTP_X_SCHOOL_ID'=>'X-School-Id') as $key=>$name) {
        if (isset($_SERVER[$key])) $headers[$name] = $_SERVER[$key];
    }
    try {
        $dispatch = require __DIR__.'/spot-cycle.backend.inc.php';
        if (!is_callable($dispatch)) { http_response_code(503); return; }
        $reply = call_user_func($dispatch, array('method'=>$method,
            'path'=>'/school/lessons/diana19'.(($suffix === '') ? '/' : $suffix),
            'headers'=>$headers, 'body'=>$body));
        if (!is_array($reply) || !isset($reply['status'], $reply['body'], $reply['headers']) ||
            !is_int($reply['status']) || $reply['status'] < 200 || $reply['status'] > 599 ||
            !is_string($reply['body']) || !is_array($reply['headers'])) {
            http_response_code(502); return;
        }
        if ($reply['status'] === 200 && ($suffix === '' || $suffix === '/')) {
            $reply['body'] = preg_replace_callback('~\b(src|href)="assets/([a-z0-9.-]+)"~',
                function($match) {
                    return $match[1].'="/index.php?route='.rawurlencode('practice/diana19/assets/'.$match[2]).'"';
                }, $reply['body']);
            $config = json_encode(array('catalog'=>'/index.php?route=practice%2Fdiana19%2Fcatalog',
                'lesson'=>'/index.php?route=practice%2Fdiana19%2Flesson%2F',
                'answer'=>'/index.php?route=practice%2Fdiana19%2Fanswer%2F'),
                JSON_HEX_TAG|JSON_HEX_AMP|JSON_HEX_APOS|JSON_HEX_QUOT);
            $reply['body'] = str_replace('<head>', '<head><script type="application/json" id="diana19-host">'.$config.'</script>', $reply['body']);
        }
    } catch (Throwable $error) { http_response_code(503); return; }
    http_response_code($reply['status']);
    foreach ($reply['headers'] as $name=>$value) {
        if (in_array(strtolower($name), array('content-type','content-security-policy','x-robots-tag'), true) &&
            is_string($value) && strpos($value, "\r") === false && strpos($value, "\n") === false) header($name.': '.$value);
    }
    header('Cache-Control: private, no-store, max-age=0');
    echo $reply['body'];
}
