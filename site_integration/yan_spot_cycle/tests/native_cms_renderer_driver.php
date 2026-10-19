<?php
// Runs only on a newly built neutral fixture, never on the uploaded CMS.
require $argv[1].'/include/native-cms-interface-fixture.inc.php';
require $argv[1].'/include/diana19.cms-renderer.inc.php';
require $argv[1].'/include/diana19.application.inc.php';
session_start();
function check_native($condition, $message) {
    if (!$condition) throw new RuntimeException($message);
}
function call_native_application() {
    global $cms, $db, $rout;
    http_response_code(200);
    ob_start();
    bridge_diana_application('practice/diana19', $cms, $db, $rout);
    return array(http_response_code(), ob_get_clean());
}
$_SERVER['REQUEST_METHOD'] = 'GET';
$_SESSION['isadmin'] = 'NEUTRAL_ADMIN_FIXTURE';
$renderer = bridge_diana_native_cms_renderer();
$application = array('cms'=>$cms, 'db'=>$db, 'router'=>$rout, 'session'=>&$_SESSION);
$view = array('title'=>'Neutral <module>', 'stage'=>'STAGING',
    'fragment_html'=>'<section class="bridge-diana19">NEUTRAL_LEARNER_SLOT</section>',
    'head_html'=>'<meta name="bridge-school-csrf-token" content="NEUTRAL_FIXTURE_VALUE">',
    'styles'=>array('/index.php?route=practice%2Fdiana19%2Fassets%2Fapplication.css'),
    'scripts'=>array('/index.php?route=practice%2Fdiana19%2Fassets%2Fdiana19.js'));
$pageData = array('preserved'=>'original');
$level = ob_get_level();
$html = $renderer($view, $application);
check_native($cms->calls === array('page', 'includeView:practice'), 'native renderer lifecycle');
check_native($pageData === array('preserved'=>'original') && ob_get_level() === $level, 'render context restoration');
foreach (array('native-session-contract-fixture', 'native-footer-fixture', 'NEUTRAL_LEARNER_SLOT',
               'Neutral &lt;module&gt;', 'NEUTRAL_FIXTURE_VALUE') as $marker) {
    check_native(strpos($html, $marker) !== false, 'native shell or slot missing');
}
foreach (array('<!doctype html>', '</head>', 'legacy-jquery-fixture.js',
               'assets%2Fapplication.css', 'assets%2Fdiana19.js') as $marker) {
    check_native(substr_count($html, $marker) === 1, 'duplicated document or assets');
}
check_native(strpos($html, 'NEUTRAL_FIXTURE_VALUE') < strpos($html, '</head>'), 'head injection boundary');
$cms->calls = array();
unset($GLOBALS['bridgeDianaApplication']);
check_native(call_native_application() === array(503, ''), 'admin must not bypass missing bindings');
check_native($cms->calls === array(), 'unbound application rendered');
$GLOBALS['bridgeDianaApplication'] = array('render'=>$renderer,
    'dispatch'=>function($request, $application) { return array('status'=>403, 'headers'=>array(), 'body'=>''); });
check_native(call_native_application() === array(403, ''), 'admission rejection changed');
check_native($cms->calls === array(), 'renderer ran before admission');
$GLOBALS['bridgeDianaApplication']['dispatch'] = function($request, $application) {
    return array('status'=>200, 'headers'=>array(),
        'body'=>'<meta name="bridge-school-csrf-token" content="NEUTRAL_FIXTURE_VALUE">');
};
$cms->mapped = false;
check_native(call_native_application() === array(503, ''), 'default view must fail closed');
check_native($pageData === array('preserved'=>'original') && ob_get_level() === $level, 'mapping rejection leaked output');
$cms->mapped = true; $cms->warnOnPage = true;
check_native(call_native_application() === array(503, ''), 'CMS warning must fail closed');
check_native($pageData === array('preserved'=>'original') && ob_get_level() === $level, 'warning leaked output');
$cms->warnOnPage = false;
list($status, $html) = call_native_application();
check_native($status === 200 && strpos($html, 'native-footer-fixture') !== false, 'renderer did not recover');
echo json_encode(array('schema'=>'native-cms-renderer-fixture-evidence-v1', 'status'=>'PASS',
    'native_page_include_view_lifecycle'=>true, 'header_footer_preserved'=>true, 'one_document_and_assets'=>true,
    'missing_binding_with_admin_status'=>503, 'rejected_admission_status'=>403,
    'wrong_mapping_status'=>503, 'cms_warning_status'=>503, 'failed_render_body_empty'=>true,
    'context_restored'=>true, 'actual_cms_archive_executed'=>false, 'production_changed'=>false))."\n";
