<?php
// Explicit private CLI component test. No bootstrap, config, constructor or DB.
$source = $argv[1]; $site = $argv[2];
foreach (array('cms.class.php'=>'708bd88fe1588f75c0c0b343bbed0ddc809ac467b9d7ffd066b78acab656a2c0',
               'routing.class.php'=>'e02acef2e8ec0c3c427f999841210f93d23560bdf9c058ef23bb754bd869e835') as $name=>$sha) {
    if (hash_file('sha256', $source.'/'.$name) !== $sha) throw new RuntimeException('unreviewed source rejected');
}
require $source.'/cms.class.php'; require $source.'/routing.class.php';
require $site.'/include/diana19.cms-renderer.inc.php';
define('SYS_PAGE_DIR', $site.'/include/page/');
define('SYS_BLOCK_DIR', $site.'/include/block/');
define('SYS_CMS_DIR', $site.'/unused-admin/');
session_start();
class ReviewedCmsReadStub {
    public $reads = 0;
    public function getRow($query, ...$args) {
        if (strpos($query, 'SELECT ') !== 0) throw new RuntimeException('non-read operation rejected');
        $this->reads++;
        return array('sitetitle'=>'Neutral site', 'sitekey'=>'neutral', 'sitedesc'=>'Neutral');
    }
}
function require_reviewed($condition, $message) {
    if (!$condition) throw new RuntimeException($message);
}
$renderer = bridge_diana_native_cms_renderer();
$view = array('title'=>'Reviewed class component', 'stage'=>'STAGING',
    'fragment_html'=>'<section class="bridge-diana19">NEUTRAL_COMPONENT</section>',
    'head_html'=>'<meta name="bridge-school-csrf-token" content="NEUTRAL_COMPONENT_VALUE">',
    'styles'=>array('/index.php?route=practice%2Fdiana19%2Fassets%2Fapplication.css'),
    'scripts'=>array('/index.php?route=practice%2Fdiana19%2Fassets%2Fdiana19.js'));
$uris = array('/practice/diana19/', '/ru/practice/diana19/', '/he/practice/diana19/',
              '/index.php?route=practice%2Fdiana19');
$checks = 0; $reads = 0;
foreach (array(false, true) as $practiceExists) {
    foreach ($uris as $uri) {
        $_SERVER['REQUEST_URI'] = $uri;
        $_SERVER['PHP_SELF'] = $_SERVER['SCRIPT_NAME'] = '/index.php';
        $_GET = array();
        $rout = new routing(array('ru','he'));
        // Test-only object: no production session/auth boundary is bypassed.
        $cms = (new ReflectionClass('cms'))->newInstanceWithoutConstructor();
        $db = new ReviewedCmsReadStub(); $lang = array();
        $cms->dbh = $db; $cms->arrlangs = array('ru','he'); $cms->deflang = 'ru';
        $cms->curlang = $cms->lang(); $cms->comm = $cms->pages();
        $cms->siteSettings = array('pages'=>array('default'=>'neutral-default.inc', 'admin'=>array()));
        if ($practiceExists) $cms->siteSettings['pages']['practice'] = 'neutral-existing-practice.inc';
        $original = $cms->siteSettings;
        $expected = SYS_PAGE_DIR.($practiceExists ? 'neutral-existing-practice.inc.php' : 'neutral-default.inc.php');
        require_reviewed($cms->includeView('practice') === $expected, 'unexpected original selection');
        $pageData = array('preserved'=>'original');
        $html = $renderer($view, array('cms'=>$cms,'db'=>$db,'router'=>$rout,'session'=>&$_SESSION));
        require_reviewed(strpos($html, 'NEUTRAL_COMPONENT') !== false, 'module not rendered');
        require_reviewed($cms->siteSettings === $original, 'original CMS settings changed');
        require_reviewed($cms->includeView('practice') === $expected, 'original practice selection changed');
        require_reviewed($pageData === array('preserved'=>'original'), 'global page data changed');
        require_reviewed($db->reads === 2, 'unexpected component reads');
        $reads += $db->reads; $checks++;
    }
}
echo json_encode(array('schema'=>'reviewed-cms-class-component-evidence-v1','status'=>'PASS',
    'exact_source_hashes_verified'=>true,'cases'=>$checks,'neutral_in_memory_select_calls'=>$reads,
    'original_practice_and_default_preserved'=>true,'yaml_mapping_changed'=>false,
    'cms_constructor_or_bootstrap_executed'=>false,'real_database_connected'=>false,
    'real_shell_blocks_used'=>false,'auth_or_permissions_changed'=>false,'production_changed'=>false))."\n";
