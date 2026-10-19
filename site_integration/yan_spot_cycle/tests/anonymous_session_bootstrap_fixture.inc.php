<?php
// Explicit CI fixture only. Not Yan's actual CMS or production session broker.
require __DIR__.'/../include/native-cms-interface-fixture.inc.php';
require_once __DIR__.'/../include/diana19.cms-renderer.inc.php';
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
    'render' => bridge_diana_native_cms_renderer()
);
