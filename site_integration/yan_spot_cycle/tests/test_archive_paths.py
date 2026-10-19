"""Prove nested/case-aware path detection without opening credential entries."""
import hashlib
import zipfile

from site_integration.yan_spot_cycle.audit_source_archive import inspect
from site_integration.yan_spot_cycle.build_candidate import original


def test_nested_and_case_variant_bootstrap_is_found_without_reading_it(tmp_path, monkeypatch):
    archive = tmp_path/'paths.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr('package/nested/index.php', original('index.php.txt'))
        output.writestr('package/nested/CMS/BootStrap.Inc.PHP', 'DO_NOT_READ_BOOTSTRAP')
        output.writestr('package/nested/cms/config.php', 'DO_NOT_READ_CONFIG')
    real_read = zipfile.ZipFile.read
    def limited_read(source, name, *args, **kwargs):
        path = name.filename if isinstance(name, zipfile.ZipInfo) else name
        assert path == 'package/nested/index.php'
        return real_read(source, name, *args, **kwargs)
    monkeypatch.setattr(zipfile.ZipFile, 'read', limited_read)
    result = inspect(archive, hashlib.sha256(archive.read_bytes()).hexdigest(), archive.stat().st_size, 3)
    assert result['bootstrap_cms_suffix_matches_case_insensitive'] == ['package/nested/CMS/BootStrap.Inc.PHP']
    assert result['bootstrap_absent_from_all_archive_paths'] is False
    index = result['known_front_controllers'][0]
    assert index['exact_target_present'] is False
    assert index['case_insensitive_targets'] == ['package/nested/CMS/BootStrap.Inc.PHP']
    assert result['credentials_values_read'] is False


def test_backup_filename_is_not_the_required_bootstrap(tmp_path):
    archive = tmp_path/'backup-only.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr('site/index.php', original('index.php.txt'))
        output.writestr('site/cms/bootstrap.inc.php.bak', 'DO_NOT_READ_BACKUP')
    result = inspect(archive, hashlib.sha256(archive.read_bytes()).hexdigest(), archive.stat().st_size, 2)
    assert result['bootstrap_absent_from_all_archive_paths'] is True
    assert result['known_front_controllers'][0]['exact_include'] == "require 'cms/bootstrap.inc.php';"
