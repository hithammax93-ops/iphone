# build_engine.py
import PyInstaller.__main__

PyInstaller.__main__.run([
    "main_engine.py",
    "--onefile",
    "--name", "main_engine",
    "--console",
    "--noconfirm",
    "--clean",
    # إضافة وحداتنا كـ hidden imports لضمان تضمينها
    "--hidden-import", "device_manager",
    "--hidden-import", "android_to_ios_converter",
    "--hidden-import", "ios_to_android_converter",
    "--hidden-import", "ios_manifest_injector",
    "--hidden-import", "Crypto.Cipher.AES",
    "--hidden-import", "Crypto.Protocol.KDF",
    "--collect-submodules", "Crypto",
])
