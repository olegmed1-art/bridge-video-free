<?php
// Called only after the actual application bootstrap and its session_start().
require_once __DIR__.'/diana19.route.inc.php';

function bridge_diana_application_reply($reply) {
    if (!is_array($reply) || !isset($reply['status'], $reply['headers'], $reply['body']) ||
        !is_int($reply['status']) || $reply['status'] < 200 || $reply['status'] > 599 ||
        !is_array($reply['headers']) || !is_string($reply['body'])) {
        throw new RuntimeException('invalid application backend reply');
    }
    return $reply;
}

function bridge_diana_application($route, $cms, $db, $rout) {
    header('Cache-Control: private, no-store, max-age=0');
    header('X-Robots-Tag: noindex, nofollow');
    if (session_status() !== PHP_SESSION_ACTIVE) { http_response_code(503); return; }
    $bindings = require __DIR__.'/diana19.application-bindings.inc.php';
    if (!is_array($bindings) || !isset($bindings['dispatch'], $bindings['render']) ||
        !is_callable($bindings['dispatch']) || !is_callable($bindings['render'])) {
        http_response_code(503); return;
    }
    // Native application objects and server session stay inside trusted PHP.
    // They are never JSON-serialized or derived from learner/school headers.
    $application = array('cms'=>$cms, 'db'=>$db, 'router'=>$rout, 'session'=>&$_SESSION);
    $dispatch = function($request) use ($bindings, $application) {
        return bridge_diana_application_reply(call_user_func($bindings['dispatch'], $request, $application));
    };
    $suffix = substr($route, strlen('practice/diana19'));
    $method = isset($_SERVER['REQUEST_METHOD']) ? $_SERVER['REQUEST_METHOD'] : 'GET';
    try {
        if ($suffix !== '' && $suffix !== '/' && $suffix !== '/assets/application.css') {
            bridge_diana_route($route, $dispatch); return;
        }
        if ($method !== 'GET') { http_response_code(405); return; }
        $headers = array();
        foreach (array('HTTP_COOKIE'=>'Cookie', 'HTTP_ORIGIN'=>'Origin',
            'HTTP_X_LEARNER_ID'=>'X-Learner-Id', 'HTTP_X_SCHOOL_ID'=>'X-School-Id') as $key=>$name) {
            if (isset($_SERVER[$key])) $headers[$name] = $_SERVER[$key];
        }
        // Admission happens before reading the fragment or calling the CMS view.
        // Existing backend verifies the session and supplies its own CSRF value.
        $reply = $dispatch(array('method'=>'GET', 'path'=>'/school/lessons/diana19/',
            'headers'=>$headers, 'body'=>''));
        if ($reply['status'] !== 200) { http_response_code($reply['status']); return; }
        if ($suffix === '/assets/application.css') {
            header('Content-Type: text/css; charset=utf-8');
            echo file_get_contents(__DIR__.'/../css/diana19.application.css'); return;
        }
        if (!preg_match('~<meta name="bridge-school-csrf-token" content="([^"]+)">~', $reply['body'], $match)) {
            throw new RuntimeException('verified lesson CSRF context unavailable');
        }
        $csrf = html_entity_decode($match[1], ENT_QUOTES, 'UTF-8');
        if (strlen($csrf) > 512 || strpos($csrf, "\r") !== false || strpos($csrf, "\n") !== false) {
            throw new RuntimeException('invalid verified CSRF context');
        }
        $config = json_encode(array('catalog'=>'/index.php?route=practice%2Fdiana19%2Fcatalog',
            'lesson'=>'/index.php?route=practice%2Fdiana19%2Flesson%2F',
            'answer'=>'/index.php?route=practice%2Fdiana19%2Fanswer%2F'),
            JSON_HEX_TAG|JSON_HEX_AMP|JSON_HEX_APOS|JSON_HEX_QUOT);
        $view = array('title'=>'Диана19 — учебные разборы', 'stage'=>'STAGING',
            'fragment_html'=>file_get_contents(__DIR__.'/../page/diana19.fragment.inc.php'),
            'head_html'=>'<meta name="bridge-school-csrf-token" content="'.htmlspecialchars($csrf, ENT_QUOTES, 'UTF-8').'">'.
                '<script type="application/json" id="diana19-host">'.$config.'</script>',
            'styles'=>array('/index.php?route=practice%2Fdiana19%2Fassets%2Fapplication.css'),
            'scripts'=>array('/index.php?route=practice%2Fdiana19%2Fassets%2Fdiana19.js'));
        $html = call_user_func($bindings['render'], $view, $application);
        if (!is_string($html)) throw new RuntimeException('CMS renderer must return its document');
        header('Content-Type: text/html; charset=utf-8');
        // The existing CMS renderer retains its document security headers.
        echo $html;
    } catch (Throwable $error) { http_response_code(503); }
}
