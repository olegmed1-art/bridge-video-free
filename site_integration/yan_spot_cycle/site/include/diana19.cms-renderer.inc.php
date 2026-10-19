<?php
// Adapter for the statically confirmed FishArt page()/includeView() contract.
// This factory installs no auth, dispatch, YAML mapping, account or journal.
function bridge_diana_native_cms_renderer() {
    return function($view, $application) {
        if (session_status() !== PHP_SESSION_ACTIVE || !is_array($view) || !is_array($application) ||
            !isset($view['stage'], $view['fragment_html'], $view['head_html'], $view['styles'], $view['scripts'], $view['title']) ||
            $view['stage'] !== 'STAGING' || !is_string($view['fragment_html']) || !is_string($view['head_html']) ||
            !is_array($view['styles']) || !is_array($view['scripts']) || !is_string($view['title'])) {
            throw new RuntimeException('native CMS view context unavailable');
        }
        foreach (array('cms'=>'cms', 'db'=>'db', 'router'=>'rout') as $key=>$global) {
            if (!array_key_exists($key, $application) || !array_key_exists($global, $GLOBALS) ||
                $application[$key] !== $GLOBALS[$global]) {
                throw new RuntimeException('native CMS objects unavailable');
            }
        }
        $cms = $application['cms'];
        if (!is_object($cms) || !is_callable(array($cms, 'page')) || !is_callable(array($cms, 'includeView'))) {
            throw new RuntimeException('native CMS renderer unavailable');
        }
        // The reviewed cms class has no __clone hook. Keep this operation inert.
        $selector = '__bridge_diana19_renderer_v1';
        if (method_exists($cms, '__clone') || !isset($cms->siteSettings['pages']) ||
            !is_array($cms->siteSettings['pages']) ||
            array_key_exists($selector, $cms->siteSettings['pages']) ||
            (isset($cms->siteSettings['pages']['admin']) &&
             (!is_array($cms->siteSettings['pages']['admin']) ||
              array_key_exists($selector, $cms->siteSettings['pages']['admin'])))) {
            throw new RuntimeException('native CMS private selector unavailable');
        }
        // Copy-on-write mapping exists only on this clone, not in YAML, the
        // global CMS or public /practice routing. Never replace a site key.
        $moduleCms = clone $cms;
        $moduleCms->siteSettings['pages'][$selector] = 'diana19.cms-view.inc';
        $expected = realpath(__DIR__.'/page/diana19.cms-view.inc.php');
        if ($expected === false) throw new RuntimeException('native module view unavailable');
        $level = ob_get_level();
        $hadPage = array_key_exists('pageData', $GLOBALS);
        $previousPage = $hadPage ? $GLOBALS['pageData'] : null;
        set_error_handler(function($severity) {
            if (!(error_reporting() & $severity)) return false;
            throw new RuntimeException('native CMS render warning');
        });
        try {
            // No CMS/admin fallback: the scoped module must resolve exactly
            // to our fixed view. A default/login/other view is never included.
            ob_start();
            $pageData = $cms->page();
            $selected = $moduleCms->includeView($selector);
            $unexpected = ob_get_clean();
            if ($unexpected !== '' || !is_array($pageData) || !is_string($selected) || realpath($selected) !== $expected) {
                throw new RuntimeException('native CMS module mapping unavailable');
            }
            $GLOBALS['pageData'] = $pageData;
            $bridgeDianaCmsView = $view;
            ob_start();
            include $expected;
            return ob_get_clean();
        } finally {
            while (ob_get_level() > $level) ob_end_clean();
            restore_error_handler();
            if ($hadPage) $GLOBALS['pageData'] = $previousPage;
            else unset($GLOBALS['pageData']);
        }
    };
}
