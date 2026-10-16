<?php
// Fails closed after fixture bootstrap/session; never calls a backend.
putenv('BRIDGE_APPLICATION_FIXTURE_UNBOUND=1');
$_GET['route'] = 'practice/diana19';
$_SERVER['REQUEST_METHOD'] = 'GET';
register_shutdown_function(function() {
    fwrite(STDERR, 'APPLICATION_UNBOUND_STATUS='.http_response_code()."\n");
});
require $argv[1].'/index.php';
