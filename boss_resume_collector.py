#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BOSS直聘简历自动筛选收藏脚本

功能说明：
- 自动扫描BOSS直聘客户端简历列表
- 识别"1+1"匹配的简历：任一公司关键词 + 任一职位关键词
  （公司关键词如 华为/华为技术有限公司/华为云；
    职位关键词如 客户/销售/拓展/业务/bd/售前/客户经理/渠道）
- 自动点击进入简历详情页（只点击黑色文字，跳过灰色/已点击的）
- 黑名单关键词：列表页该条简历所在行命中任一黑名单词则直接跳过（不点开、不收藏）
- 点击"收藏"按钮（跳过已收藏的）
- 关闭详情页，继续下一份

使用前提：
1. BOSS直聘客户端已打开并显示简历列表页
2. 客户端窗口最大化或至少简历列表区域完全可见
3. 窗口不要被其他窗口遮挡
"""

import pyautogui
import time
import os
import re
import sys
import threading
import numpy as np
from cnocr import CnOcr

# ==================== 安全设置 ====================
# 将鼠标快速移到屏幕左上角可立即停止脚本
pyautogui.FAILSAFE = True
# 每次操作之间的默认暂停时间
pyautogui.PAUSE = 0.3

# ==================== 姓名识别与收藏日志 ====================
# 点开简历后，候选人姓名在屏幕上的位置（占屏幕宽/高的比例：左, 上, 右, 下）
# 默认值按 2560x1600 全屏 BOSS 实测标定：姓名在详情页左上角、头像右侧
NAME_REGION = (0.255, 0.045, 0.42, 0.088)

# 收藏日志文件：记录已经收藏过的候选人姓名，一行一个（UTF-8）
NAME_LOG_PATH = os.path.join(os.path.expanduser('~'), 'BOSS_ResumeCollector', 'collected_names.log')

# 明显不是姓名的状态词，避免把"刚刚活跃"这类文字当成名字
NAME_BLACKLIST = {
    '刚刚活跃', '在线', '离线', '活跃', '新', '牛人', '求职者', '简历', '应聘',
    '沟通中', '已沟通', '不合适', '已收藏', '收藏', '转发', '举报', '打招呼',
    '帮我联系', '求职', '在职', '离职', '考虑机会', '随时到岗', '月内到岗',
}


class BossResumeCollector:
    def __init__(self):
        self.screen_width, self.screen_height = pyautogui.size()
        # 红线大约在屏幕正中间偏左一点，只扫描红线右侧区域
        # 左侧是候选人列表（忽略），右侧是简历详情/工作经历区域（扫描）
        self.scan_left = int(self.screen_width * 0.48)  # 红线右侧起始X坐标
        self.scan_top = 0
        self.processed_signatures = set()  # 已处理简历的签名，用于去重
        self.processed_positions = []      # 已处理简历的坐标(x,y)，用于坐标去重
        self.company_keywords = ['华为','华为技术有限公司','华为云', '华为终端']    # 匹配的公司关键词
        # 职位关键词：短词放前面，长词放后面，避免"客户"比"客户经理"先匹配
        self.keywords = ['客户经理', '行业代表', '渠道', '售前', '拓展', '业务', '客户', '销售', 'bd', '代表', '行业']    # 匹配的职位关键词
        # 黑名单关键词：在简历列表页，该条简历【所在行】出现任一黑名单关键词时，
        # 直接跳过（不点开、不收藏），继续筛选下一位。
        # 默认为空 = 不启用过滤；可在 GUI 界面随时增删。
        self.blacklist_keywords = []
        self.max_empty_scroll = 5          # 连续未找到目标时的最大滚动次数
        self.scroll_count = 0              # 当前连续未找到目标的滚动次数
        self.total_collected = 0           # 总收藏数
        self.stop_event = threading.Event()  # 停止标志：置位后主循环退出

        # ---- 姓名识别区域 & 收藏日志 ----
        self.name_region = NAME_REGION     # 姓名在屏幕上的位置（比例）
        self.name_log_path = NAME_LOG_PATH # 已收藏姓名日志文件

        print("=" * 60)
        print("正在初始化OCR引擎，首次加载需要 10~30 秒，请耐心等待...")
        print("=" * 60)

        # 初始化 CnOcr（轻量中文OCR，不依赖Paddle）
        self.ocr = CnOcr()

        print("OCR引擎加载完成！")
        print(f"屏幕分辨率: {self.screen_width} x {self.screen_height}")

        # 载入收藏日志
        existing = self.load_collected_names()
        print(f"收藏日志: {self.name_log_path}")
        if existing:
            print(f"日志中已有 {len(existing)} 个已收藏候选人姓名，收藏前会自动跳过")
        else:
            print("收藏日志为空，本次运行将记录新收藏的候选人姓名")

    # ==================== 屏幕操作 ====================

    def capture_screen(self):
        """截取红线右侧区域并转换为 numpy RGB 数组"""
        # 只截取红线右侧区域（左侧候选人列表不扫描）
        screenshot = pyautogui.screenshot(
            region=(self.scan_left, self.scan_top,
                    self.screen_width - self.scan_left, self.screen_height)
        )
        img = np.array(screenshot)  # PIL Image -> numpy RGB array
        return img

    def ocr_screen(self):
        """
        对红线右侧区域进行OCR识别
        返回: [{box, text, confidence, cx, cy, left, right, top, bottom}, ...]
        坐标已转换为屏幕绝对坐标（加上了 scan_left 偏移）
        """
        img = self.capture_screen()
        results = self.ocr.ocr(img)

        if not results:
            return []

        items = []
        for item in results:
            text = item.get('text', '')
            score = item.get('score', 0.0)
            position = item.get('position', [])

            if position is None or len(position) < 4:
                continue

            # position: [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
            # 加上 scan_left 偏移，转换为屏幕绝对坐标
            xs = [p[0] + self.scan_left for p in position]
            ys = [p[1] + self.scan_top for p in position]
            cx = sum(xs) / len(xs)
            cy = sum(ys) / len(ys)

            items.append({
                'box': list(zip(xs, ys)),
                'text': text,
                'confidence': score,
                'cx': cx,
                'cy': cy,
                'left': min(xs),
                'right': max(xs),
                'top': min(ys),
                'bottom': max(ys)
            })
        return items

    # ==================== 颜色检测 ====================

    def is_text_black(self, item, img):
        """
        判断文字是否为黑色（未点击状态）
        已点击的简历文字会变为灰色

        采样文字框内【最暗】的像素来判断（避免单点采到白色空隙误判）：
        - 黑色文字：最暗像素 RGB 最大值 < 100
        - 灰色文字：最暗像素亮度中等（100~210）且通道差异小

        注意：item 坐标是屏幕绝对坐标，img 是截取区域（红线右侧），
              需要将坐标减去 scan_left 偏移才能在 img 中正确采样。
        """
        h, w = img.shape[:2]
        # 将屏幕绝对坐标转换为截取区域相对坐标
        xs = [int(p[0]) - self.scan_left for p in item['box']]
        ys = [int(p[1]) - self.scan_top for p in item['box']]
        left = max(0, min(xs))
        right = min(w - 1, max(xs))
        top = max(0, min(ys))
        bottom = min(h - 1, max(ys))

        if right <= left or bottom <= top:
            # 框太小，退化为取中心点
            cx = int(min(w - 1, max(0, item['cx'] - self.scan_left)))
            cy = int(min(h - 1, max(0, item['cy'] - self.scan_top)))
            px = img[cy, cx]
            r, g, b = int(px[0]), int(px[1]), int(px[2])
        else:
            region = img[top:bottom + 1, left:right + 1]
            # 每个像素取 RGB 最大值，再找全局最暗的那个像素（文字笔画）
            maxchan = region.max(axis=2)
            yy, xx = np.unravel_index(np.argmin(maxchan), maxchan.shape)
            px = region[yy, xx]
            r, g, b = int(px[0]), int(px[1]), int(px[2])

        max_val = max(r, g, b)
        min_val = min(r, g, b)
        diff = max_val - min_val
        avg = (r + g + b) / 3

        is_black = max_val < 100
        is_gray = 100 <= avg <= 210 and diff < 40

        print(f"    颜色检测: 框({left},{top})-({right},{bottom}) 最暗像素 RGB=({r},{g},{b}) "
              f"avg={avg:.0f} diff={diff} → 黑色={is_black}, 灰色={is_gray}")

        return is_black and not is_gray

    def is_green_text(self, item, img):
        """
        判断文字是否为绿色（用于识别白底绿字的"刷新"按钮）。
        绿色像素判定：G 通道显著高于 R 和 B。
        item 坐标是屏幕绝对坐标，img 是截取区域（红线右侧），需减偏移。
        """
        h, w = img.shape[:2]
        xs = [int(p[0]) - self.scan_left for p in item['box']]
        ys = [int(p[1]) - self.scan_top for p in item['box']]
        left = max(0, min(xs))
        right = min(w - 1, max(xs))
        top = max(0, min(ys))
        bottom = min(h - 1, max(ys))

        if right <= left or bottom <= top:
            return False

        region = img[top:bottom + 1, left:right + 1]
        r = region[:, :, 0].astype(int)
        g = region[:, :, 1].astype(int)
        b = region[:, :, 2].astype(int)

        # 绿色像素：G 通道显著高于 R 和 B
        green_mask = (g > r + 30) & (g > b + 30)
        ratio = float(green_mask.mean())

        print(f"    绿色检测: '{item['text']}' 绿色像素占比={ratio:.0%}")
        return ratio > 0.3  # 绿色像素占比超过 30%

    # ==================== 目标识别 ====================

    def is_processed_position(self, x, y, threshold=60):
        """判断坐标是否已被处理过（同一份简历在同一屏内坐标是稳定的）"""
        for px, py in self.processed_positions:
            if abs(x - px) < threshold and abs(y - py) < threshold:
                return True
        return False

    def _match_keyword(self, text):
        """在给定文本里找命中的职位关键词（不区分大小写），找不到返回 None
        优先匹配长词（如"客户经理"），避免"客户"比"客户经理"先匹配导致信息丢失。"""
        low = text.lower()
        for kw in self.keywords:
            if kw.lower() in low:
                return kw
        return None

    def match_blacklist(self, text):
        """
        在给定文本里找命中的黑名单关键词（不区分大小写）。
        命中返回该关键词，未命中返回 None。
        """
        if not text or not self.blacklist_keywords:
            return None
        low = text.lower()
        for kw in self.blacklist_keywords:
            if kw and kw.lower() in low:
                return kw
        return None

    def _row_text(self, hw, items):
        """
        取简历列表页里【与该条简历同一行】的所有文字，拼成一行文本。
        用于黑名单过滤：只要这一行出现任一黑名单关键词，就跳过该条简历。
        """
        texts = [hw['text']]
        hw_h = hw['bottom'] - hw['top']
        for other in items:
            if other is hw:
                continue
            # 同一行：垂直方向重叠度足够高（重叠超过较小框高度的一半）
            overlap = min(other['bottom'], hw['bottom']) - max(other['top'], hw['top'])
            if overlap <= 0:
                continue
            min_h = min(hw_h, other['bottom'] - other['top'])
            if min_h <= 0 or overlap < 0.5 * min_h:
                continue
            texts.append(other['text'])
        return ' '.join(texts)

    def _match_keyword_after_company(self, text):
        """
        在【公司关键词右侧】的文本里找职位关键词。
        即：先定位公司关键词（华为/华为云/华为技术有限公司等）的位置，
        只在其右侧的字符串里匹配职位关键词（客户/销售/...）。
        """
        low = text.lower()
        company_end = -1
        for k in self.company_keywords:
            idx = low.find(k.lower())
            if idx != -1:
                company_end = max(company_end, idx + len(k))
        if company_end == -1:
            return None
        # 只取公司关键词右侧的字符串
        after = low[company_end:]
        for kw in self.keywords:
            if kw.lower() in after:
                return kw
        return None

    def _find_keyword_nearby(self, hw, items):
        """
        在公司关键词条目【同一行、右侧】的条目里找职位关键词。
        严格限制：只匹配与"华为"同一行、且位于其右侧的文字，
        避免把上方/下方其他行的"客户/销售"误判进来。
        """
        hw_h = hw['bottom'] - hw['top']
        for other in items:
            if other is hw:
                continue

            # 同一行：垂直方向重叠度足够高（重叠超过较小框高度的一半）
            overlap = min(other['bottom'], hw['bottom']) - max(other['top'], hw['top'])
            if overlap <= 0:
                continue
            min_h = min(hw_h, other['bottom'] - other['top'])
            if min_h <= 0 or overlap < 0.5 * min_h:
                continue

            # 在公司关键词右侧：职位关键词的左边界不能比华为右边界靠左太多
            if other['left'] < hw['right'] - 40:
                continue

            kw = self._match_keyword(other['text'])
            if kw:
                print(f"    附近匹配: '{other['text']}' → 命中关键词 '{kw}'")
                return kw
        return None

    def find_targets(self, items, img):
        """
        从OCR结果中找出符合条件的简历：
        - 公司关键词（华为 / 华为云 / 华为技术有限公司 等）任一命中
        - 且公司关键词【右侧、同一行】有职位关键词（客户 / 销售 / 拓展 / 业务 等）
        - 文字为黑色（未点击；灰色=已点击，跳过）
        - 该条简历所在行不含任何黑名单关键词（命中即跳过，不点开）
        """
        # 调试：打印所有OCR结果，方便排查
        print(f"    [调试] 本屏共识别到 {len(items)} 个文字块")
        for item in items:
            print(f"      → '{item['text']}'  位置({item['left']:.0f},{item['top']:.0f})-({item['right']:.0f},{item['bottom']:.0f})")

        company_items = [item for item in items
                         if any(k.lower() in item['text'].lower() for k in self.company_keywords)]
        print(f"    本屏识别到含公司关键词的条目 {len(company_items)} 个")
        targets = []

        for hw in company_items:
            # 1) 颜色过滤：只点黑色（未点击）
            if not self.is_text_black(hw, img):
                print(f"    跳过灰色文字: {hw['text']}")
                continue

            # 2) 找职位关键词：只找"华为"右侧、同一行的
            #    先看公司关键词所在条目自身右侧（OCR 常把"华为 · 客户经理"读成同一行）
            #    再看同一行右侧的相邻条目
            keyword_text = self._match_keyword_after_company(hw['text'])
            if keyword_text:
                print(f"    自身右侧匹配: '{hw['text']}' → 命中关键词 '{keyword_text}'")
            else:
                keyword_text = self._find_keyword_nearby(hw, items)

            if keyword_text is None:
                print(f"    '华为'右侧未发现职位关键词，跳过: {hw['text']}")
                continue

            # 3) 生成唯一签名，双去重（签名 + 坐标）
            signature = f"{hw['text']}_{keyword_text}"
            if signature in self.processed_signatures or self.is_processed_position(hw['cx'], hw['cy']):
                print(f"    已处理过，跳过: {signature}")
                continue

            # 4) 黑名单过滤：该条简历所在行出现任一黑名单关键词 → 跳过（不点开、不收藏）
            row_text = self._row_text(hw, items)
            hit = self.match_blacklist(row_text)
            if hit:
                print(f"    🚫 黑名单命中「{hit}」，跳过该简历（不点开）: {row_text}")
                # 记为已处理，避免每轮重复提示
                self.processed_signatures.add(signature)
                self.processed_positions.append((hw['cx'], hw['cy']))
                continue

            targets.append({
                'x': hw['cx'],
                'y': hw['cy'],
                'huawei_text': hw['text'],
                'keyword_text': keyword_text,
                'signature': signature
            })

        return targets

    # ==================== 姓名识别 & 收藏日志 ====================

    def _clean_name(self, text):
        """
        从 OCR 文本里提取可能的姓名：
        - 在分隔符/数字/括号处截断，只取第一段（如 "朱昱 · 刚刚活跃" -> "朱昱"、"26岁" -> ""）
        - 只保留中文与英文字母
        """
        if not text:
            return ''
        s = text.strip()
        s = re.split(r'[·•|,，、;；/\\\s0-9（()）\[\]【】]+', s)[0]
        s = ''.join(ch for ch in s if '\u4e00' <= ch <= '\u9fff' or ch.isalpha())
        return s

    def read_candidate_name(self):
        """
        读取当前简历详情页里的候选人姓名。
        做法：截取屏幕左上方的姓名区域做 OCR，取【最上面一行里最靠左】的文字块。
        BOSS 简历头部布局是「[头像] 姓名  状态词」，所以姓名一定是最左上的那块文字。
        识别失败返回 None（此时按正常流程继续，不做跳过）。
        """
        l = int(self.screen_width * self.name_region[0])
        t = int(self.screen_height * self.name_region[1])
        r = int(self.screen_width * self.name_region[2])
        b = int(self.screen_height * self.name_region[3])
        if r <= l or b <= t:
            return None

        try:
            shot = pyautogui.screenshot(region=(l, t, r - l, b - t))
        except Exception as e:
            print(f"  ⚠ 姓名区域截图失败: {e}")
            return None

        img = np.array(shot)
        try:
            results = self.ocr.ocr(img)
        except Exception as e:
            print(f"  ⚠ 姓名区域 OCR 失败: {e}")
            return None
        if not results:
            print("  ⚠ 姓名区域未识别到文字")
            return None

        region_h = max(1, b - t)
        best = None  # (排序键, 姓名)
        for item in results:
            text = item.get('text', '')
            pos = item.get('position', [])
            if pos is None or len(pos) < 4:
                continue
            left = min(float(p[0]) for p in pos)
            top = min(float(p[1]) for p in pos)
            name = self._clean_name(text)
            if not name:
                continue
            if name in NAME_BLACKLIST:
                print(f"    姓名区域忽略状态词: '{text}'")
                continue
            if not (2 <= len(name) <= 6):
                continue
            row = int(top // max(1.0, region_h * 0.5))  # 行分组：先比行，再比左右
            key = (row, left)
            if best is None or key < best[0]:
                best = (key, name, text)

        if best is None:
            print("  ⚠ 姓名区域未找到像姓名的文字块")
            return None
        print(f"    姓名区域识别: '{best[2]}' → 姓名【{best[1]}】")
        return best[1]

    def load_collected_names(self):
        """读取收藏日志里的姓名集合（每次实时读取，手动编辑日志也能立即生效）"""
        names = set()
        try:
            if os.path.exists(self.name_log_path):
                with open(self.name_log_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        n = self._clean_name(line)
                        if n:
                            names.add(n)
        except Exception as e:
            print(f"  ⚠ 读取收藏日志失败: {e}")
        return names

    def is_name_collected(self, name):
        """判断该姓名是否已在收藏日志中"""
        if not name:
            return False
        return name in self.load_collected_names()

    def append_collected_name(self, name):
        """把新收藏的候选人姓名追加到日志文件"""
        if not name:
            return
        try:
            os.makedirs(os.path.dirname(self.name_log_path), exist_ok=True)
            with open(self.name_log_path, 'a', encoding='utf-8') as f:
                f.write(name + '\n')
            print(f"  📝 已记录姓名【{name}】到收藏日志")
        except Exception as e:
            print(f"  ⚠ 写入收藏日志失败: {e}")

    @staticmethod
    def clear_name_log(path=None):
        """清空收藏日志（返回是否成功）"""
        p = path or NAME_LOG_PATH
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, 'w', encoding='utf-8') as f:
                f.write('')
            return True
        except Exception:
            return False

    # ==================== 页面交互 ====================

    def click_at(self, x, y, desc=""):
        """在指定坐标点击"""
        if desc:
            print(f"  点击【{desc}】位置: ({x:.0f}, {y:.0f})")
        pyautogui.click(int(x), int(y))

    def is_detail_page_open(self, items):
        """判断详情页是否已打开（通过检测是否有'收藏'或'工作经历'文字）"""
        for item in items:
            if '收藏' in item['text'] or '工作经历' in item['text']:
                return True
        return False

    def check_and_collect(self):
        """
        检查详情页收藏状态并执行收藏（严格按顺序，绝不盲目点击）：
        1. 反复 OCR，直到明确识别到"收藏"或"已收藏"
        2. "已收藏" → 跳过，返回 False
        3. "收藏" → 点击，等待后二次确认状态，返回是否新收藏
        如果多次重试仍识别不到，安全返回（不点击任何位置，避免误点窗口）
        """
        max_retry = 6

        for attempt in range(1, max_retry + 1):
            items = self.ocr_screen()

            already_item = None
            collect_item = None
            for item in items:
                if '已收藏' in item['text']:
                    already_item = item
                elif '收藏' in item['text']:
                    collect_item = item

            # 1) 先判断"已收藏"（优先，避免把"已收藏"误判成"收藏"重复点击）
            if already_item is not None:
                print("  已识别到'已收藏'，该简历此前已收藏，跳过点击")
                return False

            # 2) 只有明确识别到"收藏"（未收藏状态），才点击
            if collect_item is not None:
                self.click_at(collect_item['cx'], collect_item['cy'], "收藏")
                time.sleep(1.2)
                # 二次确认：点击后再识别一次，验证是否变为"已收藏"
                confirm_items = self.ocr_screen()
                for it in confirm_items:
                    if '已收藏' in it['text']:
                        print("  ✓ 收藏成功（已确认变为'已收藏'）")
                        return True
                print("  → 已点击'收藏'（未确认到'已收藏'，视为已处理）")
                return True

            # 3) 本轮没识别到"收藏"，等待后重试（不点击任何位置）
            print(f"  ⏳ 第 {attempt}/{max_retry} 次未识别到'收藏'按钮，等待后重试...")
            time.sleep(1.2)

        # 重试耗尽仍未识别到，安全返回，绝不盲目点击预估位置
        print("  ⚠ 多次识别仍未找到'收藏'按钮，安全跳过（不点击预估位置）")
        return False

    def close_detail_page(self):
        """关闭简历详情页——点击右侧灰色空白区域返回列表"""
        # 点击红框所示的右侧灰色空白区域（详情面板右边缘外）
        x = int(self.screen_width * 0.92)
        y = int(self.screen_height * 0.35)
        self.click_at(x, y, "右侧空白处返回")
        return True

    def scroll_down(self):
        """向下滚动页面加载更多简历"""
        scroll_x = int(self.screen_width * 0.5)
        scroll_y = int(self.screen_height * 0.6)
        pyautogui.scroll(-1000, scroll_x, scroll_y)  # 大幅滚动（原-100的10倍）
        print("  ↓ 向下滚动加载更多简历...")
        time.sleep(1.5)
        # 滚动后所有简历位置变化，清空坐标去重记录（保留签名去重兜底）
        self.processed_positions.clear()

    def check_and_click_refresh(self, items, img):
        """
        检测底部是否出现"刷新"按钮（白底绿字），出现则点击并等待页面刷新。
        优先精确匹配文本"刷新"（排除"刷新获取最新列表"这类提示文字），
        兜底用绿色文字颜色判断。返回 True 表示已点击刷新。
        """
        # 1) 优先：文本精确为"刷新"（排除"刷新获取最新列表"等长文本）
        for item in items:
            if item['text'].strip() == '刷新':
                print(f"\n  🔄 检测到'刷新'按钮，位置({item['cx']:.0f}, {item['cy']:.0f})，点击刷新...")
                self.click_at(item['cx'], item['cy'], "刷新按钮")
                self._after_refresh()
                return True

        # 2) 兜底：含"刷新"且文字为绿色（白底绿字按钮）
        for item in items:
            if '刷新' in item['text'] and self.is_green_text(item, img):
                print(f"\n  🔄 检测到绿色'刷新'按钮（'{item['text']}'），位置({item['cx']:.0f}, {item['cy']:.0f})，点击刷新...")
                self.click_at(item['cx'], item['cy'], "刷新按钮(绿色)")
                self._after_refresh()
                return True

        return False

    def _after_refresh(self):
        """点击刷新后的处理：等待加载 + 清空去重记录"""
        print("  ⏳ 等待页面刷新加载...")
        time.sleep(4)
        # 刷新后清空所有去重记录（新列表全新开始）
        self.processed_signatures.clear()
        self.processed_positions.clear()
        print("  ✓ 页面刷新完成，清空去重记录，重新开始扫描")

    # ==================== 主流程 ====================

    def process_target(self, target):
        """处理单份目标简历"""
        print(f"\n  >>> 发现目标: 【{target['huawei_text']}】 + 【{target['keyword_text']}】")

        # 1. 点击"华为"进入详情页
        self.click_at(target['x'], target['y'], target['huawei_text'])
        time.sleep(2.5)  # 等待详情页加载

        # 2. 确认详情页已打开（最多重试几次，避免误判）
        opened = False
        for _ in range(6):
            items = self.ocr_screen()
            if self.is_detail_page_open(items):
                opened = True
                break
            time.sleep(1.0)

        if not opened:
            print("  ⚠ 多次确认详情页未打开，按ESC安全返回...")
            pyautogui.press('esc')
            time.sleep(1)
            return False

        print("  详情页已打开")

        # 3. 读取候选人姓名，并检查是否已在收藏日志中（已收藏过则直接跳过）
        name = self.read_candidate_name()
        if name:
            if self.is_name_collected(name):
                print(f"  ⏭ 姓名【{name}】已在收藏日志中，跳过收藏，继续筛选下一位")
                self.close_detail_page()
                time.sleep(1.5)
                self.processed_signatures.add(target['signature'])
                self.processed_positions.append((target['x'], target['y']))
                return True
            print(f"  → 姓名【{name}】未在日志中，继续执行收藏")
        else:
            print("  ⚠ 未能识别到候选人姓名，按正常流程处理")

        # 4. 检查收藏状态并执行收藏
        collected = self.check_and_collect()
        time.sleep(1)

        # 5. 关闭详情页
        self.close_detail_page()
        time.sleep(1.5)

        # 6. 标记为已处理（签名 + 坐标双记录）
        self.processed_signatures.add(target['signature'])
        self.processed_positions.append((target['x'], target['y']))
        if collected:
            self.total_collected += 1
            print(f"  ✓ 收藏成功！累计收藏: {self.total_collected} 份")
            if name:
                self.append_collected_name(name)
        else:
            print(f"  → 已跳过（该简历此前已收藏）。累计收藏: {self.total_collected} 份")
        return True

    def reset(self):
        """重置运行状态，支持停止后重新启动（复用已加载的 OCR 引擎，无需重新加载）"""
        self.processed_signatures.clear()
        self.processed_positions.clear()
        self.total_collected = 0
        self.scroll_count = 0
        self.stop_event.clear()

    def run(self):
        """主循环"""
        print("\n" + "=" * 60)
        print("  BOSS直聘简历自动筛选收藏脚本")
        print("=" * 60)
        print(f"\n【扫描区域】")
        print(f"  只扫描屏幕右侧区域（X ≥ {self.scan_left}，约占屏幕右半部分）")
        print(f"  左侧候选人列表将被完全忽略")
        print(f"\n【筛选条件】")
        print(f"  公司关键词: {'、'.join(self.company_keywords)}")
        print(f"  职位关键词: {'、'.join(self.keywords)}")
        print("  匹配规则: 公司关键词 + 其右侧同一行的职位关键词 = 命中（不区分大小写）")
        print("  仅点击黑色文字（灰色=已点击，自动跳过）")
        if self.blacklist_keywords:
            print(f"  黑名单关键词: {'、'.join(self.blacklist_keywords)}")
            print("  黑名单规则: 列表页该条简历所在行出现任一黑名单词 → 直接跳过（不点开）")
        else:
            print("  黑名单关键词: （未设置）")
        print(f"\n【收藏日志】")
        print(f"  文件: {self.name_log_path}")
        print(f"  点开简历后先读取姓名，若姓名已在日志中则跳过收藏，避免重复收藏")
        print(f"  新收藏成功的候选人姓名会自动追加到日志")
        print("\n【使用提示】")
        print("1. 请确保BOSS直聘客户端已打开，并显示简历列表")
        print("2. 建议将客户端窗口最大化，避免被其他窗口遮挡")
        print("3. 脚本运行期间，请勿遮挡或移动客户端窗口")
        print("4. 如需紧急停止：将鼠标快速移到屏幕左上角即可")
        print("5. 按 Ctrl+C 也可以停止脚本")
        print("\n3秒后开始执行...")
        print("=" * 60)

        time.sleep(3)
        cycle = 0

        try:
            while not self.stop_event.is_set():
                cycle += 1
                print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                print(f"【第 {cycle} 轮扫描】")
                print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

                # 截屏并OCR识别
                img = self.capture_screen()
                items = self.ocr_screen()

                # 先检测底部是否出现"刷新"按钮
                if self.check_and_click_refresh(items, img):
                    self.scroll_count = 0
                    continue

                targets = self.find_targets(items, img)

                # 过滤已处理的
                new_targets = [t for t in targets if t['signature'] not in self.processed_signatures]

                if not new_targets:
                    print("  当前屏幕未发现新的符合条件的简历")
                    self.scroll_down()
                    self.scroll_count += 1

                    if self.scroll_count >= self.max_empty_scroll:
                        print(f"\n  连续 {self.max_empty_scroll} 次未找到新简历，")
                        print("  可能已到达列表底部，继续尝试滚动...")
                        self.scroll_count = 0
                    continue

                # 重置滚动计数
                self.scroll_count = 0

                # 依次处理每个目标
                for target in new_targets:
                    success = self.process_target(target)
                    if not success:
                        continue

                print(f"\n  本轮完成，继续扫描下一批...")

        except KeyboardInterrupt:
            pass

        print("\n\n" + "=" * 60)
        print("  脚本已停止")
        print(f"  本次共收藏 {self.total_collected} 份简历")
        print("=" * 60)


def main():
    try:
        collector = BossResumeCollector()
        collector.run()
    except Exception as e:
        print(f"\n脚本运行出错: {e}")
        import traceback
        traceback.print_exc()
        input("\n按回车键退出...")
        sys.exit(1)


if __name__ == '__main__':
    main()
