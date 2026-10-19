<?php
// Implements only the non-secret interfaces in the static source report.
// Not an execution/copy of the uploaded CMS, its database or configuration.
class BridgeNativeCmsInterfaceFixture {
    public $trace;
    public $siteSettings = array('pages'=>array('default'=>'default-not-module', 'practice'=>'existing-practice'));
    public $mapped = true;
    public $warnOnPage = false;
    public function __construct() { $this->trace = (object)array('calls'=>array()); }
    public function page() {
        $this->trace->calls[] = 'page';
        if ($this->warnOnPage) trigger_error('neutral fixture warning', E_USER_WARNING);
        return array('sitetitle'=>'Fixture site', 'sitekey'=>'fixture', 'sitedesc'=>'Neutral fixture');
    }
    public function includeView($key) {
        $this->trace->calls[] = 'includeView:'.$key;
        if ($this->mapped && isset($this->siteSettings['pages'][$key])) {
            return __DIR__.'/page/'.$this->siteSettings['pages'][$key].'.php';
        }
        return __DIR__.'/page/default-not-module.php';
    }
    public function lang() { return 'ru'; }
}
$cms = new BridgeNativeCmsInterfaceFixture(); $db = null; $rout = new stdClass(); $lang = array();
if (!defined('SYS_BLOCK_DIR')) define('SYS_BLOCK_DIR', __DIR__.'/block');
