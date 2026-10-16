<?php
// Fixed route mapping. The existing backend admits every page and asset request.
function bridge_spot_error($status) {
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: private, no-store');
    echo json_encode(array('detail' => 'lesson temporarily unavailable'));
}

function bridge_spot_url($suffix, $activity = null) {
    $script = isset($_SERVER['SCRIPT_NAME']) ? $_SERVER['SCRIPT_NAME'] : '/index.php';
    if (!preg_match('~^/(?:[A-Za-z0-9_.-]+/)*index\.php$~D', $script)) {
        $script = '/index.php';
    }
    if ($activity === null && isset($_GET['activity']) && is_string($_GET['activity'])) $activity = $_GET['activity'];
    $url = $script.'?route='.rawurlencode('practice/spot-cycle'.$suffix);
    if ($activity !== null) $url .= '&activity='.rawurlencode($activity);
    return $url;
}

function bridge_spot_route($route) {
    $base = 'practice/spot-cycle';
    $assets = array('learner-presentation.json', 'spot-learner.js',
        'teacher-client.js', 'teacher-review.js', 'store-transport.js',
        'lesson-ui.js', 'session-host.js', 'styles.css', 'figma.css', 'transport.css');
    $suffix = substr($route, strlen($base));
    $asset = null;
    if ($suffix === '' || $suffix === '/') {
        $target = '/school/lessons/spot-cycle/';
    } elseif ($suffix === '/transport' || $suffix === '/saved-answer') {
        $target = '/school/lessons/spot-cycle'.$suffix;
    } elseif (strpos($suffix, '/assets/') === 0 &&
              in_array(substr($suffix, 8), $assets, true)) {
        $asset = substr($suffix, 8);
        $target = '/school/lessons/spot-cycle/'.$asset;
    } else {
        bridge_spot_error(404); return;
    }
    if (isset($_GET['activity'])) {
        if (!is_string($_GET['activity']) || !in_array($_GET['activity'], array('spot.a7c2', 'spot.f3b8'), true)) {
            bridge_spot_error(403); return;
        }
        $target .= '?activity='.rawurlencode($_GET['activity']);
    }
    $method = isset($_SERVER['REQUEST_METHOD']) ? $_SERVER['REQUEST_METHOD'] : 'GET';
    $expected = $suffix === '/transport' ? 'POST' : 'GET';
    if ($method !== $expected) {
        header('Allow: '.$expected); bridge_spot_error(405); return;
    }
    $body = $method === 'POST'
        ? file_get_contents(PHP_SAPI === 'cli' ? 'php://stdin' : 'php://input', false, null, 0, 32769)
        : '';
    if ($body === false || strlen($body) > 32768) {
        bridge_spot_error(413); return;
    }
    $headers = array();
    foreach (array('HTTP_COOKIE' => 'Cookie', 'HTTP_ORIGIN' => 'Origin',
        'HTTP_X_CSRF_TOKEN' => 'X-CSRF-Token', 'CONTENT_TYPE' => 'Content-Type',
        'HTTP_X_LEARNER_ID' => 'X-Learner-Id', 'HTTP_X_SCHOOL_ID' => 'X-School-Id') as $key => $name) {
        if (isset($_SERVER[$key])) $headers[$name] = $_SERVER[$key];
    }
    try {
        $dispatch = require __DIR__.'/spot-cycle.backend.inc.php';
        if (!is_callable($dispatch)) { bridge_spot_error(503); return; }
        $reply = call_user_func($dispatch, array('method' => $method,
            'path' => $target, 'headers' => $headers, 'body' => $body));
        if (!is_array($reply) || !isset($reply['status'], $reply['headers'], $reply['body']) ||
            !is_int($reply['status']) || $reply['status'] < 200 || $reply['status'] > 599 ||
            !is_array($reply['headers']) || !is_string($reply['body'])) {
            bridge_spot_error(502); return;
        }
        if ($reply['status'] === 200) {
            if ($asset === 'session-host.js') {
                $reply['body'] = file_get_contents(dirname(__DIR__).'/js/spot-cycle-site-host.js');
            } elseif ($asset === 'learner-presentation.json' || $asset === 'spot-learner.js') {
                $reply['body'] = file_get_contents(dirname(__DIR__).'/lesson-assets/'.$asset);
            } elseif ($suffix === '' || $suffix === '/') {
                if (strpos($reply['body'], '<head>') === false) { bridge_spot_error(502); return; }
                $reply['body'] = preg_replace_callback(
                    '~\b(src|href)="([a-z0-9.-]+\.(?:js|css))"~',
                    function ($match) use ($assets) {
                        return in_array($match[2], $assets, true)
                            ? $match[1].'="'.htmlspecialchars(bridge_spot_url('/assets/'.$match[2]), ENT_QUOTES, 'UTF-8').'"'
                            : $match[0];
                    }, $reply['body']);
                $config = json_encode(array('transport' => bridge_spot_url('/transport'),
                    'savedAnswer' => bridge_spot_url('/saved-answer'),
                    'presentation' => bridge_spot_url('/assets/learner-presentation.json')),
                    JSON_HEX_TAG | JSON_HEX_AMP | JSON_HEX_APOS | JSON_HEX_QUOT);
                $reply['body'] = str_replace('<head>', '<head>' . "\n" .
                    '<script type="application/json" id="bridge-lesson-host">'.$config.'</script>', $reply['body']);
                if (preg_match('~<meta name="bridge-local-assigned-activities" content="([a-z0-9.,]+)">~', $reply['body'], $local)) {
                    $links = '<nav aria-label="Назначенные задания"><strong>Локальная учебная сессия</strong> ';
                    foreach (explode(',', $local[1]) as $id) {
                        if (!in_array($id, array('spot.a7c2', 'spot.f3b8'), true)) continue;
                        $label = $id === 'spot.a7c2' ? 'Упражнение 1' : 'Упражнение 2';
                        $links .= '<a href="'.htmlspecialchars(bridge_spot_url('', $id), ENT_QUOTES, 'UTF-8').'">'.$label.'</a> ';
                    }
                    $reply['body'] = str_replace('<body>', '<body>'.$links.'</nav>', $reply['body']);
                }
            }
            if (!is_string($reply['body'])) { bridge_spot_error(503); return; }
        }
    } catch (Throwable $error) {
        bridge_spot_error(503); return;
    }
    http_response_code($reply['status']);
    foreach ($reply['headers'] as $name => $value) {
        if (in_array(strtolower($name), array('content-type', 'cache-control',
            'content-security-policy', 'x-robots-tag'), true) && is_string($value) &&
            strpos($value, "\r") === false && strpos($value, "\n") === false) {
            header($name.': '.$value);
        }
    }
    header('Cache-Control: private, no-store, max-age=0');
    echo $reply['body'];
}
