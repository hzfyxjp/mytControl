# -*- mode: python ; coding: utf-8 -*-
"""魔云腾中控打包：只跟入口导入图，排除未用第三方与 Qt 模块。"""

block_cipher = None

# 不 collect-all：让 Analysis 沿 main.py 导入图走；仅补少数动态导入
hiddenimports = [
    "certifi",
]

# 开发依赖、以及当前程序未引用的 Qt 大模块（摇树）
excludes = [
    "pytest",
    "_pytest",
    "py",
    "pluggy",
    "iniconfig",
    "unittest",
    "test",
    "tests",
    "doctest",
    "tkinter",
    "_tkinter",
    "turtle",
    "IPython",
    "jupyter",
    "notebook",
    "matplotlib",
    "numpy",
    "pandas",
    "scipy",
    "PIL",
    "Pillow",
    "cv2",
    "torch",
    "tensorflow",
    # 本仓库未接入投屏入口，勿打入
    "app.screen_mirror",
    # PySide6：只用 Widgets/Gui/Core，其余整块排除
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DRender",
    "PySide6.QtAsyncio",
    "PySide6.QtBluetooth",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtDesigner",
    "PySide6.QtGraphs",
    "PySide6.QtGraphsWidgets",
    "PySide6.QtHelp",
    "PySide6.QtHttpServer",
    "PySide6.QtLocation",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtNetworkAuth",
    "PySide6.QtNfc",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning",
    "PySide6.QtPrintSupport",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickTest",
    "PySide6.QtQuickWidgets",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSensors",
    "PySide6.QtSerialBus",
    "PySide6.QtSerialPort",
    "PySide6.QtSpatialAudio",
    "PySide6.QtSql",
    "PySide6.QtStateMachine",
    "PySide6.QtSvg",
    "PySide6.QtSvgWidgets",
    "PySide6.QtTest",
    "PySide6.QtTextToSpeech",
    "PySide6.QtUiTools",
    "PySide6.QtWebChannel",
    "PySide6.QtWebEngine",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebSockets",
    "PySide6.QtWebView",
    "PySide6.QtXml",
    "PySide6.scripts",
    "shiboken6_generator",
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# 再滤一遍：分析阶段仍可能拖进未用 Qt / 测试包
_DROP_PREFIXES = (
    "PySide6.Qt3D",
    "PySide6.QtWebEngine",
    "PySide6.QtQuick",
    "PySide6.QtQml",
    "PySide6.QtMultimedia",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtGraphs",
    "PySide6.QtPdf",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtSensors",
    "PySide6.QtPositioning",
    "PySide6.QtLocation",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtOpenGL",
    "PySide6.QtSvg",
    "PySide6.QtXml",
    "PySide6.QtHttpServer",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSerial",
    "PySide6.QtSpatialAudio",
    "PySide6.QtStateMachine",
    "PySide6.QtTextToSpeech",
    "PySide6.QtUiTools",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
    "PySide6.QtWebView",
    "PySide6.QtNetworkAuth",
    "PySide6.QtPrintSupport",
    "PySide6.QtAsyncio",
    "pytest",
    "_pytest",
    "app.screen_mirror",
)


def _keep(name: str) -> bool:
    return not any(name == p or name.startswith(p + ".") for p in _DROP_PREFIXES)


a.pure = [x for x in a.pure if _keep(x[0])]
a.binaries = [x for x in a.binaries if _keep(x[0])]
a.datas = [x for x in a.datas if _keep(x[0])]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MoyuntengControl",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="MoyuntengControl",
)
