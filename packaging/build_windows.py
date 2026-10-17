"""Cross-publish Windows UI and package the real Python ADB engine.

Supply an official Python Windows embeddable ZIP separately. Android SDK tools
are not redistributed: the user installs official platform-tools themselves.
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dotnet', default='dotnet')
    parser.add_argument('--runtime-zip', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='wa-windows-build-') as temp:
        package = Path(temp) / 'WhatsAppMigrator-AndroidBackup'
        package.mkdir()
        subprocess.run([args.dotnet, 'publish', str(source / 'WhatsAppMigrator.csproj'),
                        '--configuration', 'Release', '--runtime', 'win-x64',
                        '-p:EnableWindowsTargeting=true', '--self-contained', 'true',
                        '--output', str(package), '--nologo'], check=True)
        runtime = package / 'runtime'
        with zipfile.ZipFile(args.runtime_zip) as archive:
            archive.extractall(runtime)
        pth = list(runtime.glob('python*._pth'))
        if len(pth) != 1:
            raise RuntimeError('Expected one Python embeddable ._pth file')
        zip_name = next(runtime.glob('python*.zip')).name
        pth[0].write_text(f'{zip_name}\n.\n..\n', encoding='ascii')
        for name in ['main_engine.py', 'android_backup.py', 'device_manager.py',
                     'android_to_ios_converter.py', 'ios_to_android_converter.py',
                     'ios_manifest_injector.py', 'README.md', 'README.txt']:
            shutil.copy2(source / name, package / name)
        (package / 'Official-ADB-Download.url').write_text(
            '[InternetShortcut]\nURL=https://developer.android.com/tools/releases/platform-tools\n',
            encoding='ascii')
        for pdb in package.glob('*.pdb'):
            pdb.unlink()
        assert (package / 'WhatsAppMigrator.exe').read_bytes()[:2] == b'MZ'
        assert (runtime / '_sqlite3.pyd').is_file()
        assert not (package / 'demo').exists()
        inventory = {p.relative_to(package).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in sorted(package.rglob('*')) if p.is_file()}
        (package / 'PACKAGE-SHA256.json').write_text(json.dumps(inventory, indent=2) + '\n', encoding='utf-8')
        archive_path = Path(shutil.make_archive(str(output / 'WhatsAppMigrator-Windows-x64-AndroidBackup'),
                                                'zip', package.parent, package.name))
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.testzip() is None
    print(json.dumps({'archive': str(archive_path), 'bytes': archive_path.stat().st_size,
                      'sha256': hashlib.sha256(archive_path.read_bytes()).hexdigest()}, indent=2))


if __name__ == '__main__':
    main()
