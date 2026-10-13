#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BOSS 直聘简历自动筛选收藏 —— 图形界面控制台

功能：
- 图形界面内开关脚本（启动 / 停止）
- 设置全局快捷键（启动/停止、紧急停止），焦点在 BOSS 窗口也能响应
- 界面内直接查看和修改识别关键词（公司关键词 + 职位关键词 + 黑名单关键词）
- 实时显示运行日志与已收藏计数
- 关键词与快捷键自动保存到 gui_config.json，下次启动自动载入
"""

import os
import re
import sys
import json
import queue
import subprocess
import threading

# cnstd 在导入时会读取 APPDATA 环境变量（缺失会崩溃）。这里补兜底，
# 保证源码运行（如 Git Bash 环境）也能正常启动；打包后由 pack_hook 兜底。
if not os.environ.get('APPDATA'):
    os.environ['APPDATA'] = os.path.join(os.path.expanduser('~'), 'AppData', 'Roaming')

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QLabel, QLineEdit, QPushButton, QPlainTextEdit, QMessageBox
)
from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont

from boss_resume_collector import BossResumeCollector, NAME_REGION, NAME_LOG_PATH

try:
    import keyboard
    HAS_KEYBOARD = True
except Exception:
    HAS_KEYBOARD = False


CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gui_config.json')

DEFAULT_CONFIG = {
    'company_keywords': '华为、华为技术有限公司、华为云、华为终端',
    'keywords': '客户经理、行业代表、渠道、售前、拓展、业务、客户、销售、bd、代表、行业',
    'blacklist_keywords': '',
    'hotkey_toggle': 'f8',
    'hotkey_stop': 'f9',
    'name_region': '0.255,0.045,0.42,0.088',
}


def split_keywords(text):
    """把用户输入的关键词字符串拆成列表（按顿号/逗号/分号/空格/换行分隔）"""
    parts = re.split(r'[、，,;；\s]+', text or '')
    return [p for p in parts if p]


def parse_region(text):
    """解析姓名区域字符串 '左,上,右,下'（比例 0~1），非法返回 None"""
    try:
        parts = [float(x) for x in re.split(r'[，,;\s]+', (text or '').strip()) if x != '']
    except ValueError:
        return None
    if len(parts) != 4:
        return None
    l, t, r, b = parts
    if not (0 <= l < r <= 1 and 0 <= t < b <= 1):
        return None
    return (l, t, r, b)


class StdoutRedirector:
    """把 print 输出捕获到队列（供 GUI 日志框显示）。线程安全。"""
    def __init__(self, log_queue):
        self.log_queue = log_queue
        self._buffer = ''
        self._lock = threading.Lock()

    def write(self, s):
        if not s:
            return
        with self._lock:
            self._buffer += s
            while '\n' in self._buffer:
                line, self._buffer = self._buffer.split('\n', 1)
                if line.strip():
                    self.log_queue.put(line)

    def flush(self):
        pass


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('BOSS 简历自动筛选收藏 - 控制台')
        self.resize(800, 940)

        self.collector = None
        self.running = False
        self.loading = False
        self._pending_stop = False
        self.status_text = '已停止'
        self.log_queue = queue.Queue()
        self.config = self._load_config()

        # 重定向 stdout，让脚本的 print 都进日志框
        self._redirector = StdoutRedirector(self.log_queue)
        self._old_stdout = sys.stdout
        sys.stdout = self._redirector
        sys.stderr = self._redirector

        # 热键触发标志（keyboard 回调线程写入，QTimer 轮询主线程处理）
        self._toggle_flag = False
        self._stop_flag = False
        self._stat_tick = 0

        self._build_ui()
        self._apply_config_to_ui()
        self._register_hotkeys()

        # 定时轮询：日志 + 状态 + 热键标志（主线程安全更新 UI）
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._sync_ui)
        self._timer.start(100)

        print('[系统] 界面已就绪，请先检查下方关键词与快捷键，再点击「启动脚本」')

    # ==================== 配置读写 ====================

    def _load_config(self):
        cfg = dict(DEFAULT_CONFIG)
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                cfg.update(data)
            except Exception:
                pass
        return cfg

    def _save_config(self):
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f'[配置] 保存失败: {e}')

    # ==================== UI 构建 ====================

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # ---- 关键词设置 ----
        kw_box = QGroupBox('关键词设置')
        kw_layout = QVBoxLayout(kw_box)
        kw_layout.addWidget(QLabel('公司关键词（顿号 / 逗号 / 空格分隔，任一命中即算）'))
        self.company_edit = QLineEdit()
        kw_layout.addWidget(self.company_edit)
        kw_layout.addWidget(QLabel('职位关键词（出现在公司关键词右侧同一行即收藏）'))
        self.keyword_edit = QLineEdit()
        kw_layout.addWidget(self.keyword_edit)
        kw_layout.addWidget(QLabel('黑名单关键词（列表页该条简历所在行出现任一即跳过，不点开；留空=不启用）'))
        self.blacklist_edit = QLineEdit()
        self.blacklist_edit.setPlaceholderText('例如：外包、兼职、实习（留空表示不启用）')
        kw_layout.addWidget(self.blacklist_edit)
        kw_btn_row = QHBoxLayout()
        self.btn_apply_kw = QPushButton('应用关键词')
        self.btn_apply_kw.clicked.connect(self.apply_keywords)
        self.btn_default = QPushButton('恢复默认')
        self.btn_default.clicked.connect(self._restore_default)
        kw_btn_row.addWidget(self.btn_apply_kw)
        kw_btn_row.addWidget(self.btn_default)
        kw_btn_row.addStretch(1)
        kw_layout.addLayout(kw_btn_row)

        kw_layout.addWidget(QLabel('姓名识别区域（左,上,右,下，占屏幕比例；默认已适配全屏 BOSS，一般不用改）'))
        self.region_edit = QLineEdit()
        kw_layout.addWidget(self.region_edit)
        root.addWidget(kw_box)

        # ---- 收藏日志 ----
        log_box2 = QGroupBox('收藏日志（记录已收藏过的候选人姓名，收藏前自动查重）')
        log_layout2 = QVBoxLayout(log_box2)
        self.log_path_label = QLabel(f'日志文件: {NAME_LOG_PATH}')
        self.log_path_label.setWordWrap(True)
        log_layout2.addWidget(self.log_path_label)
        log_btn_row = QHBoxLayout()
        self.btn_open_log = QPushButton('打开日志')
        self.btn_open_log.clicked.connect(self.open_log)
        self.btn_clear_log = QPushButton('清空日志')
        self.btn_clear_log.clicked.connect(self.clear_log)
        self.btn_refresh_log = QPushButton('刷新统计')
        self.btn_refresh_log.clicked.connect(self._refresh_log_stat)
        log_btn_row.addWidget(self.btn_open_log)
        log_btn_row.addWidget(self.btn_clear_log)
        log_btn_row.addWidget(self.btn_refresh_log)
        log_btn_row.addStretch(1)
        log_layout2.addLayout(log_btn_row)
        self.log_stat_label = QLabel('日志中已收藏: 0 人')
        log_layout2.addWidget(self.log_stat_label)
        root.addWidget(log_box2)

        # ---- 快捷键设置 ----
        hk_box = QGroupBox('快捷键设置（全局热键，即使焦点在 BOSS 窗口也能生效）')
        hk_layout = QVBoxLayout(hk_box)
        row1 = QHBoxLayout()
        row1.addWidget(QLabel('启动/停止:'))
        self.toggle_edit = QLineEdit()
        self.toggle_edit.setMaximumWidth(180)
        row1.addWidget(self.toggle_edit)
        row1.addStretch(1)
        hk_layout.addLayout(row1)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel('紧急停止:'))
        self.stop_edit = QLineEdit()
        self.stop_edit.setMaximumWidth(180)
        row2.addWidget(self.stop_edit)
        row2.addStretch(1)
        hk_layout.addLayout(row2)
        hk_layout.addWidget(QLabel('按键写法示例：f8、f9、ctrl+s、alt+f1、ctrl+shift+s。修改后点「应用快捷键」生效。'))
        self.btn_apply_hk = QPushButton('应用快捷键')
        self.btn_apply_hk.clicked.connect(self._apply_hotkeys)
        hk_layout.addWidget(self.btn_apply_hk)
        root.addWidget(hk_box)

        # ---- 控制区 ----
        ctrl_box = QGroupBox('控制')
        ctrl_layout = QVBoxLayout(ctrl_box)
        btn_row = QHBoxLayout()
        self.btn_start = QPushButton('▶ 启动脚本')
        self.btn_start.setStyleSheet(
            'QPushButton{background-color:#2e7d32;color:white;font-weight:bold;padding:8px 22px;}'
            'QPushButton:disabled{background-color:#bdbdbd;color:#f5f5f5;}'
        )
        self.btn_start.clicked.connect(self.start)
        self.btn_stop = QPushButton('■ 停止脚本')
        self.btn_stop.setStyleSheet(
            'QPushButton{background-color:#c62828;color:white;font-weight:bold;padding:8px 22px;}'
            'QPushButton:disabled{background-color:#bdbdbd;color:#f5f5f5;}'
        )
        self.btn_stop.clicked.connect(self.stop)
        self.btn_stop.setEnabled(False)
        btn_row.addWidget(self.btn_start)
        btn_row.addWidget(self.btn_stop)
        btn_row.addStretch(1)
        ctrl_layout.addLayout(btn_row)

        info_row = QHBoxLayout()
        self.status_label = QLabel('状态: 已停止')
        self.count_label = QLabel('已收藏: 0 份')
        info_row.addWidget(self.status_label)
        info_row.addSpacing(40)
        info_row.addWidget(self.count_label)
        info_row.addStretch(1)
        ctrl_layout.addLayout(info_row)

        ctrl_layout.addWidget(QLabel('提示：启动后请保持 BOSS 客户端窗口在前台可见、不要被其他窗口遮挡。'))
        root.addWidget(ctrl_box)

        # ---- 日志区 ----
        log_box = QGroupBox('运行日志')
        log_layout = QVBoxLayout(log_box)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.document().setMaximumBlockCount(3000)
        self.log_view.setFont(QFont('Consolas', 9))
        log_layout.addWidget(self.log_view)
        root.addWidget(log_box, 1)

    def _apply_config_to_ui(self):
        self.company_edit.setText(self.config.get('company_keywords', DEFAULT_CONFIG['company_keywords']))
        self.keyword_edit.setText(self.config.get('keywords', DEFAULT_CONFIG['keywords']))
        self.blacklist_edit.setText(self.config.get('blacklist_keywords', ''))
        self.toggle_edit.setText(self.config.get('hotkey_toggle', 'f8'))
        self.stop_edit.setText(self.config.get('hotkey_stop', 'f9'))
        self.region_edit.setText(self.config.get('name_region', DEFAULT_CONFIG['name_region']))
        self._refresh_log_stat()

    def _restore_default(self):
        self.config = dict(DEFAULT_CONFIG)
        self._apply_config_to_ui()
        self._save_config()
        self._register_hotkeys()
        # 若脚本实例已存在，同步恢复默认后的关键词
        if self.collector is not None:
            self.collector.company_keywords = list(split_keywords(self.config['company_keywords']))
            self.collector.keywords = list(split_keywords(self.config['keywords']))
            self.collector.blacklist_keywords = list(split_keywords(self.config['blacklist_keywords']))
        print('[配置] 已恢复默认设置')

    # ==================== 关键词 ====================

    def apply_keywords(self):
        company = split_keywords(self.company_edit.text())
        job = split_keywords(self.keyword_edit.text())
        blacklist = split_keywords(self.blacklist_edit.text())   # 允许为空（=不启用黑名单）
        if not company:
            print('[提示] 公司关键词不能为空，未应用')
            return False
        if not job:
            print('[提示] 职位关键词不能为空，未应用')
            return False
        self.config['company_keywords'] = self.company_edit.text()
        self.config['keywords'] = self.keyword_edit.text()
        self.config['blacklist_keywords'] = self.blacklist_edit.text()
        self._save_config()
        # 若脚本实例已存在，实时更新（运行中也能即时调整关键词）
        if self.collector is not None:
            self.collector.company_keywords = list(company)
            self.collector.keywords = list(job)
            self.collector.blacklist_keywords = list(blacklist)
        print(f'[关键词] 公司: {", ".join(company)}')
        print(f'[关键词] 职位: {", ".join(job)}')
        print(f'[关键词] 黑名单: {", ".join(blacklist) if blacklist else "（未设置）"}')
        # 顺带应用姓名识别区域
        self.apply_name_region()
        return True

    # ==================== 姓名识别区域 & 收藏日志 ====================

    def apply_name_region(self):
        """解析并应用姓名识别区域（非法时保留默认，不影响关键词生效）"""
        region = parse_region(self.region_edit.text())
        if region is None:
            print('[姓名区域] 格式应为 4 个 0~1 的小数，例如 0.255,0.045,0.42,0.088；本次未应用，使用默认')
            return False
        self.config['name_region'] = self.region_edit.text().strip()
        self._save_config()
        if self.collector is not None:
            self.collector.name_region = region
        print(f'[姓名区域] 已应用: {region}')
        return True

    def _log_path(self):
        if self.collector is not None:
            return self.collector.name_log_path
        return NAME_LOG_PATH

    def _refresh_log_stat(self):
        try:
            path = self._log_path()
            count = 0
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    count = sum(1 for line in f if line.strip())
            self.log_stat_label.setText(f'日志中已收藏: {count} 人')
        except Exception:
            self.log_stat_label.setText('日志中已收藏: (读取失败)')

    def open_log(self):
        path = self._log_path()
        try:
            if not os.path.exists(path):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                open(path, 'a', encoding='utf-8').close()
            if sys.platform.startswith('win'):
                os.startfile(path)  # noqa
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', path])
            else:
                subprocess.Popen(['xdg-open', path])
            print(f'[日志] 已打开: {path}')
        except Exception as e:
            print(f'[日志] 打开失败: {e}')
            QMessageBox.warning(self, '打开失败', f'无法打开日志文件:\n{path}\n\n{e}')

    def clear_log(self):
        path = self._log_path()
        ret = QMessageBox.question(
            self, '确认清空',
            '确定要清空收藏日志吗？\n清空后，之前已收藏的候选人可能被重复收藏。',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if ret != QMessageBox.Yes:
            return
        if BossResumeCollector.clear_name_log(path):
            print(f'[日志] 已清空: {path}')
        else:
            print(f'[日志] 清空失败: {path}')
        self._refresh_log_stat()

    # ==================== 快捷键 ====================

    def _apply_hotkeys(self):
        self.config['hotkey_toggle'] = self.toggle_edit.text().strip() or 'f8'
        self.config['hotkey_stop'] = self.stop_edit.text().strip() or 'f9'
        self._save_config()
        self._register_hotkeys()

    def _register_hotkeys(self):
        if not HAS_KEYBOARD:
            print('[热键] 未检测到 keyboard 库，全局快捷键不可用，请用界面按钮控制')
            return
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            pass
        toggle = self.config.get('hotkey_toggle', 'f8')
        stop = self.config.get('hotkey_stop', 'f9')
        try:
            keyboard.add_hotkey(toggle, self._on_toggle_hotkey)
            keyboard.add_hotkey(stop, self._on_stop_hotkey)
            print(f'[热键] 已注册：启动/停止 = {toggle}，紧急停止 = {stop}')
        except Exception as e:
            print(f'[热键] 注册失败: {e}')

    def _on_toggle_hotkey(self):
        self._toggle_flag = True

    def _on_stop_hotkey(self):
        self._stop_flag = True

    # ==================== 启动 / 停止 ====================

    def start(self):
        if self.running or self.loading:
            return
        if not self.apply_keywords():
            return
        self.running = True
        self.loading = True
        self._pending_stop = False
        self._set_status('正在启动...')
        if self.collector is None:
            threading.Thread(target=self._first_run, daemon=True).start()
        else:
            self.collector.reset()
            threading.Thread(target=self._run_loop, daemon=True).start()

    def _first_run(self):
        """首次启动：初始化 OCR 引擎（耗时 10~30 秒），随后进入主循环"""
        try:
            self._set_status('正在初始化 OCR 引擎（首次约 10~30 秒，请耐心等待）...')
            c = BossResumeCollector()
            c.company_keywords = list(split_keywords(self.config.get('company_keywords', '')))
            c.keywords = list(split_keywords(self.config.get('keywords', '')))
            c.blacklist_keywords = list(split_keywords(self.config.get('blacklist_keywords', '')))
            c.name_region = parse_region(self.config.get('name_region', '')) or NAME_REGION
            self.collector = c
            if self._pending_stop:
                # 加载期间用户点了停止
                self._pending_stop = False
                self.running = False
                self.loading = False
                self._set_status('已停止')
                return
            self._run_loop()
        except Exception as e:
            self._set_status(f'启动失败: {e}')
            self.running = False
            self.loading = False

    def _run_loop(self):
        self.loading = False
        self._set_status('运行中...')
        try:
            self.collector.run()
        except Exception as e:
            print(f'[运行] 出错: {e}')
        self.running = False
        self._set_status('已停止')

    def stop(self):
        if self.collector is not None:
            self.collector.stop_event.set()
        else:
            # 还在加载 OCR，标记待停止
            self._pending_stop = True
        self._set_status('正在停止...')

    def _set_status(self, text):
        self.status_text = text

    # ==================== UI 同步（QTimer 轮询，主线程安全） ====================

    def _sync_ui(self):
        # 日志
        lines = []
        while not self.log_queue.empty():
            try:
                lines.append(self.log_queue.get_nowait())
            except queue.Empty:
                break
        if lines:
            self.log_view.appendPlainText('\n'.join(lines))
            sb = self.log_view.verticalScrollBar()
            sb.setValue(sb.maximum())

        # 状态与计数
        self.status_label.setText(f'状态: {self.status_text}')
        if self.collector is not None:
            self.count_label.setText(f'已收藏: {self.collector.total_collected} 份')

        # 收藏日志人数统计（约每 2 秒刷新一次）
        self._stat_tick += 1
        if self._stat_tick % 20 == 0:
            self._refresh_log_stat()

        # 按钮可用性
        self.btn_start.setEnabled(not self.running and not self.loading)
        self.btn_stop.setEnabled(self.running or self.loading)

        # 全局热键标志
        if self._toggle_flag:
            self._toggle_flag = False
            if self.running or self.loading:
                self.stop()
            else:
                self.start()
        if self._stop_flag:
            self._stop_flag = False
            self.stop()

    def closeEvent(self, event):
        # 关闭窗口时停止脚本并恢复 stdout
        try:
            if self.collector is not None:
                self.collector.stop_event.set()
        except Exception:
            pass
        sys.stdout = self._old_stdout
        sys.stderr = self._old_stdout
        event.accept()


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
