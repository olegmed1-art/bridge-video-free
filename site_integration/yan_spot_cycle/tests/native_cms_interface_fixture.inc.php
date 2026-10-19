<?php
// Implements only the non-secret interfaces in the static source report.
// Not an execution/copy of the uploaded CMS, its database or configuration.
class BridgeNativeCmsInterfaceFixture {
    public $calls = array();
    public $mapped = true;
    public $warnOnPage = false;
    public function page() {
        $this->calls[] = 'page';
        if ($this->warnOnPage) trigger_error('neutral fixture warning', E_USER_WARNING);
        return array('sitetitle'=>'Fixture site', 'sitekey'=>'fixture', 'sitedesc'=>'Neutral fixture');
    }
    public function includeView($key) {
        $this->calls[] = 'includeView:'.$key;
        if ($this->mapped && $key === 'practice') return __DIR__.'/page/diana19.cms-view.inc.php';
        return __DIR__.'/page/default-not-module.php';
    }
    public function lang() { return 'ru'; }
}
$cms = new BridgeNativeCmsInterfaceFixture(); $db = null; $rout = new stdClass(); $lang = array();
if (!defined('SYS_BLOCK_DIR')) define('SYS_BLOCK_DIR', __DIR__.'/block');
