# -*- mode: python ; coding: utf-8 -*-
"""Build dell'eseguibile Windows (PyInstaller).

    pyinstaller --clean --noconfirm magnetscout.spec

Produce ``dist/MagnetScout.exe``: un file unico, nessuna installazione,
nessuna dipendenza esterna. Le risorse statiche viaggiano dentro il bundle;
i dati dell'utente restano accanto all'eseguibile (vedi ``paths.py``).
"""

EXCLUDES = [
    # Moduli della libreria standard mai usati dall'applicazione.
    "tkinter", "turtle", "turtledemo", "idlelib", "lib2to3",
    "unittest", "doctest", "pydoc_data", "test",
    "ensurepip", "venv", "distutils", "setuptools", "pip",
    # Framework opzionali che PyInstaller potrebbe agganciare.
    "PIL", "numpy", "pandas", "matplotlib", "IPython",
]

a = Analysis(
    ["server.py"],
    pathex=[],
    binaries=[],
    datas=[("static", "static")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="MagnetScout",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version="packaging/version_info.txt",
)
