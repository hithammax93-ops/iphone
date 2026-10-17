import hashlib
import os
import plistlib
import sqlite3
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import pytest
import ios_manifest_injector as injector_module
from ios_manifest_injector import IOSManifestInjector


def make_backup(tmp_path, payload=None):
    db = sqlite3.connect(tmp_path / 'Manifest.db')
    db.execute('create table Files(fileID text primary key, domain text, relativePath text, flags int, file blob)')
    if payload:
        db.execute('insert into Files values(?,?,?,?,?)', payload)
    db.commit(); db.close()
    return IOSManifestInjector(str(tmp_path))


def test_uid_update_preserves_indirection_and_unknown_metadata(tmp_path):
    fid = IOSManifestInjector.compute_file_id('D', 'f')
    objects = ['$null', {'Size': plistlib.UID(2), 'Digest': plistlib.UID(3), 'ProtectionClass': plistlib.UID(4), 'Extra': 'keep'}, 1, b'old', 3]
    archive = {'$archiver': 'NSKeyedArchiver', '$version': 100000, '$objects': objects, '$top': {'root': plistlib.UID(1)}}
    inj = make_backup(tmp_path, (fid, 'D', 'f', 1, plistlib.dumps(archive, fmt=plistlib.FMT_BINARY)))
    assert inj.update_entry(fid, 5, b'new')
    result = plistlib.loads(inj.get_entry(fid)['file'])
    assert isinstance(result['$objects'][1]['Size'], plistlib.UID)
    assert result['$objects'][2] == 5 and result['$objects'][3] == b'new'
    assert result['$objects'][1]['Extra'] == 'keep'


def test_direct_integer_properties_are_scalars_and_root_is_unchanged(tmp_path):
    fid = IOSManifestInjector.compute_file_id('D', 'f')
    objects = ['$null', {'Size': 1, 'Digest': plistlib.UID(2), 'ProtectionClass': 3, 'Extra': 'keep'}, b'old', 'sentinel', 'another']
    archive = {'$archiver': 'NSKeyedArchiver', '$version': 100000, '$objects': objects, '$top': {'root': plistlib.UID(1)}}
    inj = make_backup(tmp_path, (fid, 'D', 'f', 1, plistlib.dumps(archive, fmt=plistlib.FMT_BINARY)))
    assert inj.update_entry(fid, 5, b'new')
    result = plistlib.loads(inj.get_entry(fid)['file'])
    assert result['$objects'][1]['Size'] == 5
    assert result['$objects'][1]['ProtectionClass'] == 3
    assert result['$objects'][1]['Extra'] == 'keep'
    assert result['$objects'][2] == b'new'
    assert result['$objects'][3:] == ['sentinel', 'another']


def test_invalid_archive_and_encrypted_backup_are_explicit(tmp_path):
    fid = IOSManifestInjector.compute_file_id('D', 'f')
    inj = make_backup(tmp_path, (fid, 'D', 'f', 1, b'not a plist'))
    with pytest.raises(ValueError): inj.update_entry(fid, 1, b'x')
    (tmp_path / 'Manifest.plist').write_bytes(plistlib.dumps({'IsEncrypted': True}))
    with pytest.raises(ValueError, match='Encrypted'): IOSManifestInjector(str(tmp_path))


def test_injection_is_bounded_and_rerun_upserts(tmp_path):
    inj = make_backup(tmp_path)
    source = tmp_path / 'source'; source.write_bytes(b'abc')
    fid = IOSManifestInjector.compute_file_id('D', 'f')
    # New entries require a compatible pre-existing metadata template.
    db = sqlite3.connect(tmp_path / 'Manifest.db')
    db.execute('insert into Files values(?,?,?,?,?)', (fid, 'D', 'f', 1, plistlib.dumps({'Template': 'keep'})))
    db.commit(); db.close()
    assert inj.inject_file('D', 'f', str(source)) == fid
    assert open(inj.get_backup_file_path(fid), 'rb').read() == b'abc'
    source.write_bytes(b'longer')
    assert inj.inject_file('D', 'f', str(source)) == fid
    assert open(inj.get_backup_file_path(fid), 'rb').read() == b'longer'
    assert inj.get_entry(fid)['file']


def test_paths_and_ids_rejected(tmp_path):
    inj = make_backup(tmp_path)
    with pytest.raises(ValueError): inj.compute_file_id('D', '../escape')
    with pytest.raises(ValueError): inj.compute_file_id('D', 'C:/x')
    with pytest.raises(ValueError): inj.get_backup_file_path('../bad')


def test_untemplated_new_entry_and_nonbytes_metadata_are_rejected(tmp_path):
    inj = make_backup(tmp_path)
    with pytest.raises(ValueError, match='compatible metadata template'):
        inj.add_entry('D', 'new', 1, b'x')
    fid = IOSManifestInjector.compute_file_id('D', 'bad')
    db = sqlite3.connect(tmp_path / 'Manifest.db')
    db.execute('insert into Files values(?,?,?,?,?)', (fid, 'D', 'bad', 1, 'not-bytes'))
    db.commit(); db.close()
    with pytest.raises(ValueError, match='Invalid existing'):
        inj.update_entry(fid, 1, b'x')


def test_serialize_failure_does_not_replace_existing_backup_or_metadata(tmp_path, monkeypatch):
    fid = IOSManifestInjector.compute_file_id('D', 'f')
    old_metadata = plistlib.dumps({'$archiver': 'NSKeyedArchiver', '$version': 100000,
                                   '$objects': ['$null', {'Size': plistlib.UID(2), 'Digest': plistlib.UID(3), 'ProtectionClass': plistlib.UID(4)}, 1, b'old', 3],
                                   '$top': {'root': plistlib.UID(1)}}, fmt=plistlib.FMT_BINARY)
    inj = make_backup(tmp_path, (fid, 'D', 'f', 1, old_metadata))
    dest = inj.get_backup_file_path(fid)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    (tmp_path / fid[:2] / fid).write_bytes(b'old')
    source = tmp_path / 'source'; source.write_bytes(b'new')
    monkeypatch.setattr(injector_module.NSKeyedArchiverHelper, 'serialize', staticmethod(lambda archive: (_ for _ in ()).throw(ValueError('serialize failed'))))
    with pytest.raises(ValueError, match='serialize failed'):
        inj.inject_file('D', 'f', str(source))
    assert open(dest, 'rb').read() == b'old'
    assert inj.get_entry(fid)['file'] == old_metadata
    assert not list((tmp_path / fid[:2]).glob('.*'))


def test_copy_failure_restores_existing_backup_atomically(tmp_path, monkeypatch):
    fid = IOSManifestInjector.compute_file_id('D', 'f')
    inj = make_backup(tmp_path, (fid, 'D', 'f', 1, plistlib.dumps({'Template': 'keep'})))
    dest = inj.get_backup_file_path(fid)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    open(dest, 'wb').write(b'old')
    source = tmp_path / 'source'; source.write_bytes(b'new')
    real_replace = injector_module.os.replace
    def fail_copy(src, dst):
        if src.endswith('.tmp') and dst == dest:
            raise OSError('copy failed')
        return real_replace(src, dst)
    monkeypatch.setattr(injector_module.os, 'replace', fail_copy)
    with pytest.raises(OSError, match='copy failed'):
        inj.inject_file('D', 'f', str(source))
    assert open(dest, 'rb').read() == b'old'
    assert not list((tmp_path / fid[:2]).glob('.*'))
