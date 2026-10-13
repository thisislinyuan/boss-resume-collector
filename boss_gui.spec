# -*- mode: python ; coding: utf-8 -*-
"""
BOSS直聘简历自动筛选收藏 —— GUI 版 PyInstaller 打包配置
生成单个可执行文件（onefile），内置 cnocr/cnstd 模型，离线可用。
入口为 boss_gui.py（PySide6 图形界面）。
"""
from PyInstaller.utils.hooks import collect_all

block_cipher = None

datas = []
binaries = []
hiddenimports = []

# ---- cnocr / cnstd / rapidocr：收集其全部数据文件、二进制与隐式导入 ----
for _pkg in ['cnocr', 'cnstd', 'rapidocr']:
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

# ---- keyboard（全局热键）：收集其数据文件与隐式导入 ----
_kb_d, _kb_b, _kb_h = collect_all('keyboard')
datas += _kb_d
binaries += _kb_b
hiddenimports += _kb_h

# ---- torchvision 的 C++ 扩展（新版改名为 *_stable.pyd，动态加载，必须显式打包）----
_tv_dir = 'C:/Users/hanli/WorkBuddy/2026-09-03-10-52-30/venv/Lib/site-packages/torchvision'
binaries += [
    (_tv_dir + '/_C_stable.pyd', 'torchvision'),
    (_tv_dir + '/image_stable.pyd', 'torchvision'),
    (_tv_dir + '/jpeg8.dll', 'torchvision'),
    (_tv_dir + '/libpng16.dll', 'torchvision'),
    (_tv_dir + '/libsharpyuv.dll', 'torchvision'),
    (_tv_dir + '/libwebp.dll', 'torchvision'),
    (_tv_dir + '/zlib.dll', 'torchvision'),
]

# ---- 内置 OCR 模型文件（识别模型 + 检测模型），随 exe 一起分发 ----
_model_root = 'C:/Users/hanli/AppData/Roaming'
datas += [
    (_model_root + '/cnocr/2.3/densenet_lite_136-gru/cnocr-v2.3-densenet_lite_136-gru-epoch=004-ft-model.onnx',
     'models/cnocr/2.3/densenet_lite_136-gru'),
    (_model_root + '/cnstd/1.2/ppocr/multi_PP-OCRv6_det_small/PP-OCRv6_det_small.onnx',
     'models/cnstd/1.2/ppocr/multi_PP-OCRv6_det_small'),
    (_model_root + '/cnstd/1.2/ppocr/multi_PP-OCRv6_det_small/config.yaml',
     'models/cnstd/1.2/ppocr/multi_PP-OCRv6_det_small'),
]

a = Analysis(
    ['boss_gui.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['pack_hook.py'],
    excludes=[
        # 仅排除确定用不到、体积又大的模块。
        # 注意：pandas 被 cnstd.yolov7 模块级导入，必须保留；PySide6 是 GUI 依赖，必须保留。
        'paddle', 'paddleocr', 'paddlex', 'paddlex2', 'paddledet', 'paddleseg',
        'tkinter', 'PyQt5', 'PyQt6', 'PySide2',
        'torchaudio', 'torchtext',
        'pytest', 'IPython', 'jupyter', 'notebook',
        'sphinx', 'docutils', 'Cython', 'setuptools',
    ],
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
    name='BOSS_ResumeCollector_GUI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # GUI 程序，不显示命令行窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
