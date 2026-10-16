"""Source-exact CMS adapter candidate; does not include/run the unavailable CMS."""
import argparse
import difflib
import json
from pathlib import Path
import shutil

from .build_candidate import HERE, original

APPLICATION_HOOK = """
$bridgeDianaRoute = isset($_GET['route']) && is_string($_GET['route'])
    ? trim($_GET['route'], '/') : '';
if ($bridgeDianaRoute === 'practice/diana19' ||
    strpos($bridgeDianaRoute, 'practice/diana19/') === 0) {
    require __DIR__.'/include/diana19.application.inc.php';
    bridge_diana_application($bridgeDianaRoute, $cms, isset($db) ? $db : null, $rout);
    exit;
}
"""


def build(output: Path):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError('application candidate must be new')
    index = original('index.php.txt')
    marker = b'session_start();'
    if index.count(marker) != 1 or index.index(b"require 'cms/bootstrap.inc.php';") > index.index(marker):
        raise ValueError('actual front-controller lifecycle changed')
    newline = b'\r\n' if b'\r\n' in index else b'\n'
    hook = APPLICATION_HOOK.encode().replace(b'\n', newline)
    patched = index.replace(marker, marker+hook, 1)
    output.mkdir(parents=True)
    for relative in ['include/diana19.application.inc.php', 'include/diana19.application-bindings.inc.php',
                     'include/diana19.route.inc.php', 'include/spot-cycle.backend.inc.php',
                     'page/diana19.fragment.inc.php', 'css/diana19.application.css']:
        target = output/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(HERE/'site'/relative, target)
    (output/'index.php').write_bytes(patched)
    (output/'.htaccess').write_bytes(original('htaccess.txt'))
    patch = ''.join(difflib.unified_diff(index.decode().splitlines(True), patched.decode().splitlines(True),
                                        fromfile='a/index.php', tofile='b/index.php'))
    output.with_suffix('.index.patch').write_text(patch, encoding='utf-8', newline='')
    return {'candidate': str(output), 'hook_after': 'cms/bootstrap.inc.php + session_start()',
            'document_renderer': 'EXISTING_CMS_BINDING_REQUIRED', 'cms_source_available': False,
            'local_loopback_fallback': False, 'production_activated': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    print(json.dumps(build(parser.parse_args().output)))
