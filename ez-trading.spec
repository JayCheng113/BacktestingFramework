# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['ez.api.app', 'ez.api.routes.market_data', 'ez.api.routes.backtest', 'ez.api.routes.factors', 'ez.api.deps', 'ez.strategy.builtin.ma_cross', 'ez.factor.builtin.technical', 'ez.data.providers.tushare_provider', 'ez.data.providers.tencent_provider', 'ez.data.providers.fmp_provider', 'uvicorn.logging', 'uvicorn.loops.auto', 'uvicorn.protocols.http.auto', 'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan.on']
hiddenimports += collect_submodules('uvicorn')
hiddenimports += collect_submodules('duckdb')


a = Analysis(
    ['launcher.py'],
    pathex=[],
    binaries=[],
    datas=[('web/dist', 'web/dist'), ('configs', 'configs'), ('strategies', 'strategies')],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ez-trading',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ez-trading',
)
