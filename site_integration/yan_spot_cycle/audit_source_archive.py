"""Read-only ZIP path check. Never opens bootstrap, configs, SQL or credentials.

Only a size/name-matching index.php is read as bytes and compared to the already
provided, hash-pinned public index. No other entry contents are opened.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile

EXPECTED_SHA = 'bf9ce4f396c454a1443ab78e8819e59f2d558303258a5cc15abd63fab76e235c'
EXPECTED_BYTES = 13462063
EXPECTED_ENTRIES = 2095
KNOWN_INDEX_SHA = '01a297e6b06e31c7f5059feba9e2d24fe89332346995f35d22570ecfff15dece'
KNOWN_INDEX_BYTES = 543


def normalized(name):
    return PurePosixPath(name.replace('\\', '/')).as_posix().removeprefix('./')


def inspect(archive: Path, expected_sha=EXPECTED_SHA, expected_size=EXPECTED_BYTES,
            expected_entries=EXPECTED_ENTRIES):
    archive = Path(archive)
    if archive.stat().st_size != expected_size:
        raise ValueError('source archive size mismatch')
    with archive.open('rb') as source:
        sha = hashlib.file_digest(source, 'sha256').hexdigest()
    if sha != expected_sha:
        raise ValueError('source archive SHA mismatch')
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        if len(entries) != expected_entries:
            raise ValueError('source archive entry count mismatch')
        paths = [normalized(info.filename) for info in entries if not info.is_dir()]
        cms_matches = [p for p in paths if p.casefold().endswith('/cms/bootstrap.inc.php')
                       or p.casefold() == 'cms/bootstrap.inc.php']
        all_basename_matches = [p for p in paths if PurePosixPath(p).name.casefold() == 'bootstrap.inc.php']
        indexes = []
        opened = []
        for info in entries:
            path = normalized(info.filename)
            parts = PurePosixPath(path).parts
            if (info.is_dir() or not parts or PurePosixPath(path).name.casefold() != 'index.php'
                    or info.file_size != KNOWN_INDEX_BYTES):
                continue
            # Hash bytes only. Decode/print no unmatched entry contents.
            opened.append(path)
            if hashlib.sha256(source.read(info)).hexdigest() != KNOWN_INDEX_SHA:
                continue
            target = (PurePosixPath(path).parent/'cms/bootstrap.inc.php').as_posix()
            indexes.append({'index_path': path, 'index_sha256': KNOWN_INDEX_SHA,
                'index_exact_archive_member_name': info.filename,
                'exact_include': "require 'cms/bootstrap.inc.php';",
                'expected_member_relative_to_index': target,
                'exact_target_present': target in paths,
                'case_insensitive_targets': [p for p in paths if p.casefold() == target.casefold()]})
    return {'schema': 'yan-source-archive-path-audit-v1', 'archive_sha256': sha,
        'archive_bytes': expected_size, 'zip_entries': len(entries),
        'bootstrap_cms_suffix_matches_case_insensitive': cms_matches,
        'bootstrap_basename_matches_case_insensitive': all_basename_matches,
        'bootstrap_exact_archive_member_names': [info.filename for info in entries if
            not info.is_dir() and PurePosixPath(normalized(info.filename)).name.casefold() == 'bootstrap.inc.php'],
        'known_front_controllers': indexes,
        'bootstrap_absent_from_all_archive_paths': not all_basename_matches,
        'opened_index_candidate_paths': opened,
        'bootstrap_or_config_contents_read': False, 'credentials_values_read': False,
        'archive_modified': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(inspect(args.archive), ensure_ascii=True, indent=2))
