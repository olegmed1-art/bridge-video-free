<?php
// Separate FishArt view, selected through cms::includeView('practice').
// Only the admitted application renderer supplies this trusted view model.
global $pageData, $db, $cms, $rout, $lang;
if (!isset($bridgeDianaCmsView) || !defined('SYS_BLOCK_DIR')) {
    throw new RuntimeException('native CMS shell context unavailable');
}
$blockRoot = realpath(SYS_BLOCK_DIR);
if ($blockRoot === false) throw new RuntimeException('native CMS shell unavailable');
$blocks = array();
foreach (array('s-header', 'header', 'footer', 'js', 's-footer') as $name) {
    $path = realpath($blockRoot.DIRECTORY_SEPARATOR.$name.'.inc.php');
    if ($path === false || dirname($path) !== $blockRoot || !is_file($path)) {
        throw new RuntimeException('native CMS block unavailable');
    }
    $blocks[$name] = $path;
}
$headerData = array('sitetitle'=>$bridgeDianaCmsView['title'],
    'sitekey'=>isset($pageData['sitekey']) ? $pageData['sitekey'] : '',
    'sitedesc'=>isset($pageData['sitedesc']) ? $pageData['sitedesc'] : '');
$extraHead = $bridgeDianaCmsView['head_html'];
foreach ($bridgeDianaCmsView['styles'] as $url) {
    if ($url !== '/index.php?route=practice%2Fdiana19%2Fassets%2Fapplication.css') {
        throw new RuntimeException('native module stylesheet rejected');
    }
    $extraHead .= '<link rel="stylesheet" href="'.htmlspecialchars($url, ENT_QUOTES, 'UTF-8').'">';
}
foreach ($bridgeDianaCmsView['scripts'] as $url) {
    if ($url !== '/index.php?route=practice%2Fdiana19%2Fassets%2Fdiana19.js') {
        throw new RuntimeException('native module script rejected');
    }
    $extraHead .= '<script defer src="'.htmlspecialchars($url, ENT_QUOTES, 'UTF-8').'"></script>';
}
ob_start();
include $blocks['s-header'];
$head = ob_get_clean();
if (preg_match_all('~</head\s*>~i', $head) !== 1) {
    throw new RuntimeException('native CMS head boundary unavailable');
}
echo preg_replace_callback('~</head\s*>~i', function($match) use ($extraHead) {
    return $extraHead.$match[0];
}, $head, 1);
include $blocks['header'];
// This static scoped fragment uses textContent for learner data in its JS.
echo $bridgeDianaCmsView['fragment_html'];
include $blocks['footer'];
include $blocks['js'];
include $blocks['s-footer'];
