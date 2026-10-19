<?php
// Explicit CI fixture only. Not Yan's actual CMS or production session broker.
$cms = new stdClass(); $db = null; $rout = new stdClass();
$fixturePrivate = getenv('BRIDGE_NATIVE_SESSION_FIXTURE_PRIVATE');
$fixtureDispatch = isset($GLOBALS['bridgeSpotBackendDispatch']) ? $GLOBALS['bridgeSpotBackendDispatch'] : null;
$GLOBALS['bridgeDianaApplication'] = array(
    'dispatch' => function($request, $application) use ($fixturePrivate, $fixtureDispatch) {
        if (session_status() !== PHP_SESSION_ACTIVE || !is_callable($fixtureDispatch) || !$fixturePrivate) {
            throw new RuntimeException('explicit native fixture context missing');
        }
        $isPage = $request['method'] === 'GET' && $request['path'] === '/school/lessons/diana19/';
        if ($isPage && empty($_SESSION['neutral_fixture_owner'])) {
            $_SESSION['neutral_fixture_owner'] = bin2hex(random_bytes(32));
            $_SESSION['neutral_fixture_csrf'] = bin2hex(random_bytes(32));
        }
        if (empty($_SESSION['neutral_fixture_owner']) || empty($_SESSION['neutral_fixture_csrf'])) {
            return array('status'=>403, 'headers'=>array('Content-Type'=>'application/json'), 'body'=>'{}');
        }
        $key = hash('sha256', session_id());
        $projection = json_encode(array('schema'=>'native-php-fixture-owner-v1',
            'session_sha256'=>$key, 'owner_ref'=>$_SESSION['neutral_fixture_owner'],
            'csrf_token'=>$_SESSION['neutral_fixture_csrf'],
            'origin'=>getenv('BRIDGE_SINGLE_USER_FRONTEND_ORIGIN')));
        $file = $fixturePrivate.'/session-projections/'.$key.'.json';
        if (file_put_contents($file, $projection, LOCK_EX) === false) {
            throw new RuntimeException('native fixture projection unavailable');
        }
        // On the first page the browser has not received Set-Cookie yet.
        // Forward only the session established by native PHP, never a browser
        // owner header or an unverified incoming session identifier.
        $request['headers']['Cookie'] = session_name().'='.session_id();
        return call_user_func($fixtureDispatch, $request);
    },
    'render' => function($view, $application) {
        if (session_status() !== PHP_SESSION_ACTIVE || $view['stage'] !== 'STAGING') {
            throw new RuntimeException('native fixture renderer context missing');
        }
        $head = $view['head_html'];
        foreach ($view['styles'] as $url) $head .= '<link rel="stylesheet" href="'.htmlspecialchars($url, ENT_QUOTES, 'UTF-8').'">';
        foreach ($view['scripts'] as $url) $head .= '<script defer src="'.htmlspecialchars($url, ENT_QUOTES, 'UTF-8').'"></script>';
        return '<!doctype html><html><head>'.$head.'</head><body>'.
            '<header id="native-session-contract-fixture">NATIVE_PHP_SESSION_FIXTURE_NOT_YAN_CMS</header>'.
            '<main>'.$view['fragment_html'].'</main><footer>EXISTING_SHELL_FIXTURE</footer></body></html>';
    }
);
