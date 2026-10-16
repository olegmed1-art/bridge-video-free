<?php
// CLI-only harness; never copied into the candidate web root.
if (PHP_SAPI !== 'cli') { http_response_code(404); exit; }
$_GET['route'] = $argv[1];
$_SERVER['REQUEST_METHOD'] = $argv[2];
$_SERVER['SCRIPT_NAME'] = '/index.php';
$headers = json_decode($argv[3], true);
foreach ($headers as $key => $value) $_SERVER[$key] = $value;
register_shutdown_function(function () {
    fwrite(STDERR, "\nBRIDGE_STATUS=".http_response_code()."\n");
});
if (getenv('BRIDGE_YAN_DISPATCH') === '1') {
    $GLOBALS['bridgeSpotBackendDispatch'] = function ($request) {
        $pipes = array();
        $process = proc_open(array(getenv('BRIDGE_TEST_PYTHON'), __DIR__.'/backend_fixture.py'),
            array(0 => array('pipe', 'r'), 1 => array('pipe', 'w'), 2 => array('pipe', 'w')), $pipes);
        if (!is_resource($process)) throw new RuntimeException('fixture unavailable');
        fwrite($pipes[0], json_encode($request)); fclose($pipes[0]);
        $reply = stream_get_contents($pipes[1]); fclose($pipes[1]);
        $error = stream_get_contents($pipes[2]); fclose($pipes[2]);
        if (proc_close($process) !== 0) throw new RuntimeException('fixture failed');
        return json_decode($reply, true);
    };
}
require getenv('BRIDGE_YAN_CANDIDATE').'/index.php';
