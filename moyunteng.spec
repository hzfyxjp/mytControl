# -*- mode: python ; coding: utf-8 -*-
"""魔云腾中控打包：只跟入口导入图，排除未用第三方与 Qt 模块。

不打包：adb/、官方 Win/LgCloud（c/）、webplayer 的 dashboard。
webplayer 由 build_exe.bat 按需拷到 EXE 旁。
"""

block_cipher = None

# 不 collect-all：让 Analysis 沿 main.py 导入图走；仅补少数动态导入
# QtWebEngine 启动依赖 QtPrintSupport（勿排除）
hiddenimports = [
    "certifi",
    "PySide6.QtPrintSupport",
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
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebSockets",
    "PySide6.QtWebView",
    "PySide6.QtXml",
    "PySide6.scripts",
    "shiboken6_generator",
    # 已废弃的投屏方案（ADB / 官方 Win DLL 提权）
    "app.screen_mirror_adb",
    "app.elevate",
]

# webplayer 由 build_exe.bat 只拷 play.html 链路到 EXE 旁；此处不 datas，
# 避免再进 _internal，也避免把 dashboard / adb / 官方 Win SDK(c) 打进去。
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

# 再滤一遍：分析阶段仍可能拖进未用 Qt / 测试包（保留 WebEngine 投屏）
_DROP_PREFIXES = (
    "PySide6.Qt3D",
    "PySide6.QtWebEngineQuick",
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
    "PySide6.QtWebSockets",
    "PySide6.QtWebView",
    "PySide6.QtNetworkAuth",
    "PySide6.QtAsyncio",
    "pytest",
    "_pytest",
)


def _keep(name: str) -> bool:
    return not any(name == p or name.startswith(p + ".") for p in _DROP_PREFIXES)


def _keep_data(item) -> bool:
    """禁止把本地厂商目录 / 未用 webplayer 文件打进包。"""
    name = item[0] if isinstance(item, (tuple, list)) else str(item)
    dest = item[1] if isinstance(item, (tuple, list)) and len(item) > 1 else ""
    text = f"{name}|{dest}".replace("\\", "/").lower()
    if "/adb/" in f"/{text}/" or text.endswith("/adb") or text.startswith("adb/"):
        return False
    if (
        text == "c"
        or text.startswith("c/")
        or "/c/" in f"/{text}/"
        or "lgcloud" in text
        or "myt_winsdk" in text
        or "android_window" in text
    ):
        return False
    if "webplayer/" in text or text.endswith("webplayer"):
        # dashboard / manifest 不进包；加载图由 build_exe 按需拷
        base = name.replace("\\", "/").split("/")[-1].lower()
        if base in {"dashboard.html", "dashboard.js", "manifest.json", "start.png"}:
            return False
    return _keep(name if isinstance(name, str) else str(name))


a.pure = [x for x in a.pure if _keep(x[0])]
a.binaries = [x for x in a.binaries if _keep(x[0])]
a.datas = [x for x in a.datas if _keep_data(x)]

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
