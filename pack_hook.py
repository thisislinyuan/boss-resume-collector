# -*- coding: utf-8 -*-
"""
PyInstaller 运行时钩子：
在程序主体运行前，把 cnocr / cnstd 的模型目录指向打包内置的模型，
从而让 exe 离线可用（无需联网下载模型）。
"""
import os
import sys

# frozen：打包后的 exe；否则为源码运行
if getattr(sys, 'frozen', False):
    base = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
else:
    base = os.path.dirname(os.path.abspath(__file__))

# 强制指向内置模型（识别模型 + 检测模型）
os.environ['CNOCR_HOME'] = os.path.join(base, 'models', 'cnocr')
os.environ['CNSTD_HOME'] = os.path.join(base, 'models', 'cnstd')

# cnstd 的 data_dir_default() 会被急切求值（作为 os.getenv 的默认参数），
# 若 APPDATA 缺失会抛 TypeError。这里补一个兜底值，保证任何环境下都能正常启动。
if not os.environ.get('APPDATA'):
    os.environ['APPDATA'] = os.path.join(os.path.expanduser('~'), 'AppData', 'Roaming')
