"""Build a new, isolated site tree from the two reviewed public entry files."""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
CONTENT_SHA = 'b79ae7ba7b5ce2ad3ad1cfb3377f4b8cb025001961d56f2d108d5534caef320c'
SOURCE = {
    'index.php.txt': '01a297e6b06e31c7f5059feba9e2d24fe89332346995f35d22570ecfff15dece',
    'htaccess.txt': '3e20d20575544836bcc4dc5cdf50ab805f9f1c96b99606cd9513fea1dc51375a',
}
PROMPT_SHA = {
    'spot.a7c2': '871d6bd12d348617bcce3507e4747548720d8667bc05da134e31d2410da51ac8',
    'spot.f3b8': '86ea1c43c6d1882107bea7c860f4f0dcbeb98d2c3f8dc74c9539389c1602cfa3',
}
OLD_BODY_SHA = {'spot.a7c2': '2932b39752d111ffa02d8c65be7a57b33d7f7b89195e69d5a9025143177533a4',
                'spot.f3b8': '712780972476641e2fdc3b9d5d9c9326fb7e202e516d5213d545622930cb963c'}
HOOK = """$bridgeSpotRoute = isset($_GET['route']) && is_string($_GET['route'])
    ? trim($_GET['route'], '/') : '';
if ($bridgeSpotRoute === 'practice/spot-cycle' ||
    strpos($bridgeSpotRoute, 'practice/spot-cycle/') === 0) {
    require __DIR__.'/include/spot-cycle.route.inc.php';
    bridge_spot_route($bridgeSpotRoute);
    exit;
}

$bridgeDianaRoute = isset($_GET['route']) && is_string($_GET['route'])
    ? trim($_GET['route'], '/') : '';
if ($bridgeDianaRoute === 'practice/diana19' ||
    strpos($bridgeDianaRoute, 'practice/diana19/') === 0) {
    require __DIR__.'/include/diana19.route.inc.php';
    bridge_diana_route($bridgeDianaRoute);
    exit;
}

"""


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def original(name):
    raw = (HERE / 'source-public' / name).read_bytes()
    lf = raw.replace(b'\r\n', b'\n')
    crlf = lf.replace(b'\n', b'\r\n')
    # The text handoff may add a final display newline; the ZIP hash is authority.
    for candidate in (raw, lf, crlf, lf[:-1] if lf.endswith(b'\n') else lf,
                      crlf[:-2] if crlf.endswith(b'\r\n') else crlf):
        if digest(candidate) == SOURCE[name]:
            return candidate
    raise ValueError('public source differs from audited ZIP entry: ' + name)


def project_content():
    raw = (HERE / 'content' / 'lesson.ui.json').read_bytes()
    if digest(raw) != CONTENT_SHA:
        raise ValueError('authored public package changed')
    package = json.loads(raw)
    frozen = json.loads((REPO / 'data/research/spot_card_candidate_v2/routing-handoff/student-prompts.json').read_bytes())
    prompts = {p['exercise_id']: p for p in frozen['prompts']}
    cards = []
    for exercise in package['exercises']:
        eid = exercise['exercise_id']
        if eid not in PROMPT_SHA or exercise['prompt'] != prompts[eid] or \
                exercise['prompt']['prompt_sha256'] != PROMPT_SHA[eid]:
            raise ValueError('authored prompt binding changed')
        content = exercise['lesson_content']
        choices = '\n'.join('- ' + choice['label'] for choice in content['first_lead_choices'])
        body = '\n\n'.join((content['title'], *package['shared_student_facts'],
                             content['question'], choices, content['explanation_request']))
        suits = {'S': '♠', 'H': '♥', 'D': '♦', 'C': '♣'}
        first_plays = [suits[c['card'][0]] + c['card'][1:] for c in content['first_lead_choices']]
        cards.append({'exercise_id': eid, 'source_sha256': digest(body.encode('utf-8')),
            'presentation': {'format': 'markdown', 'body': body, 'inputs': {
                'first_play': {'type': 'choice', 'options': first_plays, 'required': True},
                'count': {'type': 'choice', 'options': [c['value'] for c in content['numeric_choices']], 'required': True},
                'explanation': {'type': 'text', 'max_length': 4000, 'required': True}}}})
    if [c['exercise_id'] for c in cards] != list(PROMPT_SHA):
        raise ValueError('expected exactly the two reviewed public exercises')
    return json.dumps({'schema': 'two-spot-local-learner-presentation-v1',
        'status': 'CANDIDATE_NOT_DELIVERED', 'cards': cards},
        ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def build(output):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError('candidate destination must be new')
    index = original('index.php.txt')
    htaccess = original('htaccess.txt')
    marker = b"require 'cms/bootstrap.inc.php';"
    if index.count(marker) != 1:
        raise ValueError('ambiguous CMS entrypoint')
    newline = b'\r\n' if b'\r\n' in index else b'\n'
    hook = HOOK.encode().replace(b'\n', newline)
    patched = index.replace(marker, hook + marker, 1)
    public = project_content()
    helper = (REPO / 'bridge_school_api/static/spot_cycle_session/spot-learner.js').read_bytes()
    old_pin = b'88d8af64b387cf79294a6084d472fc5f6f0373d4c5febe7188be7883fde14cde'
    if helper.count(old_pin) != 1:
        raise ValueError('unexpected learner adapter pin')
    helper = helper.replace(old_pin, digest(public).encode())
    for card in json.loads(public)['cards']:
        previous = OLD_BODY_SHA[card['exercise_id']].encode()
        if helper.count(previous) != 1:
            raise ValueError('unexpected learner text binding')
        helper = helper.replace(previous, card['source_sha256'].encode())
    shutil.copytree(HERE / 'site', output)
    (output / 'index.php').write_bytes(patched)
    (output / '.htaccess').write_bytes(htaccess)
    assets = output / 'lesson-assets'
    assets.mkdir()
    (assets / 'learner-presentation.json').write_bytes(public)
    (assets / 'spot-learner.js').write_bytes(helper)
    patch = ''.join(difflib.unified_diff(index.decode().splitlines(True),
        patched.decode().splitlines(True), fromfile='a/index.php', tofile='b/index.php'))
    (output.parent / (output.name + '.index.patch')).write_bytes(patch.encode())
    return {'candidate': str(output), 'index_added_lines': len(HOOK.splitlines()),
            'source_package_sha256': CONTENT_SHA, 'presentation_sha256': digest(public),
            'archive_sha256': 'bf9ce4f396c454a1443ab78e8819e59f2d558303258a5cc15abd63fab76e235c'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    print(json.dumps(build(parser.parse_args().output)))
