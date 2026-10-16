<?php
// Contract fixture only. This is not Yan's unavailable CMS/bootstrap.
$cms = new stdClass(); $db = null; $rout = new stdClass();
$GLOBALS['applicationBootstrapFixtureSeen'] = true;
$originalDispatch = isset($GLOBALS['bridgeSpotBackendDispatch']) ? $GLOBALS['bridgeSpotBackendDispatch'] : null;
if (getenv('BRIDGE_APPLICATION_FIXTURE_UNBOUND') !== '1') {
    $GLOBALS['bridgeDianaApplication'] = array(
        'dispatch' => function($request, $application) use ($originalDispatch) {
            if (!isset($GLOBALS['applicationBootstrapFixtureSeen']) || session_status() !== PHP_SESSION_ACTIVE ||
                !($application['cms'] instanceof stdClass) || !is_callable($originalDispatch)) {
                throw new RuntimeException('application lifecycle not established');
            }
            if ($request['method'] === 'POST' && empty($_SESSION['fixture_page_seen'])) {
                throw new RuntimeException('native application session continuity missing');
            }
            return call_user_func($originalDispatch, $request);
        },
        'render' => function($view, $application) {
            if (session_status() !== PHP_SESSION_ACTIVE || $view['stage'] !== 'STAGING') {
                throw new RuntimeException('application session missing');
            }
            $_SESSION['fixture_page_seen'] = true;
            $head = $view['head_html'];
            foreach ($view['styles'] as $url) $head .= '<link rel="stylesheet" href="'.htmlspecialchars($url, ENT_QUOTES, 'UTF-8').'">';
            foreach ($view['scripts'] as $url) $head .= '<script defer src="'.htmlspecialchars($url, ENT_QUOTES, 'UTF-8').'"></script>';
            return '<!doctype html><html><head>'.$head.'</head><body>'.
                '<header id="cms-contract-fixture-shell">EXISTING_APPLICATION_SHELL_FIXTURE</header>'.
                '<main id="cms-contract-fixture-content">'.$view['fragment_html'].'</main>'.
                '<footer id="cms-contract-fixture-footer">EXISTING_APPLICATION_FOOTER_FIXTURE</footer></body></html>';
        }
    );
}
