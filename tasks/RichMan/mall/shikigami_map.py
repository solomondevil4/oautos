# -*- coding: utf-8 -*-
"""
寄售屋 · 式神名归一化（OCR 方案）

**新增式神只需改本文件的 SHIKIGAMI_NAMES** —— GUI 下拉框的枚举由它动态生成
（见 tasks/RichMan/config.py 的 ConsignShikigami），OCR 模糊匹配也用它，一处改动全局生效。

素材规则由 json + dev_tools/assets_extract.py 生成，落在 tasks/RichMan/assets.py 的
I_CONSIGN_* / O_CONSIGN_SHIKIGAMI_LIST，与名单无关，加式神不需要动素材。

为什么需要归一化：竖排美术字经 OCR 会出现形近字错误，例如
    灶门祢豆子 -> 灶门称豆子   （"祢"/"称"）
直接用 keyword 精确匹配会找不到，所以走三级匹配：精确 -> 别名表 -> 模糊匹配。
"""
import difflib

# 全部可选式神（GUI 下拉选项 + OCR 模糊匹配基准，本文件是唯一数据源）
SHIKIGAMI_NAMES = [
    "初音未来",
    "巡音流歌",
    "夏目&猫老师",
    "犬夜叉",
    "杀生丸",
    "桔梗",
    "鬼灯",
    "卖药郎",
    "奴良陆生",
    "夜刀神",
    "神乐&定春",
    "坂田银时",
    "嘴平伊之助",
    "我妻善逸",
    "灶门祢豆子",
    "灶门炭治郎",
    "黑崎一护",
    "朽木露琪亚",
    "阿香",
    "蜜桃&芥子",
    "闻人翊悬",
    "申屠子夜",
    "镜音铃·连",
]

# OCR 已知的误识别：发现新的就往里加一行
OCR_ALIAS = {
    "灶门称豆子": "灶门祢豆子",
}

# 模糊匹配阈值。实测"灶门称豆子"对正确名 0.800，对第二名"灶门炭治郎"仅 0.400，
# 中间有很宽的安全带，取 0.75 既能纠错又不会误配。
FUZZY_THRESHOLD = 0.75


def normalize_shikigami(ocr_text: str) -> str | None:
    """
    把 OCR 文本归一化到标准式神名

    :param ocr_text: detect_and_ocr 返回的原始文本
    :return: 标准式神名；不在名单内或相似度过低返回 None
    """
    if not ocr_text:
        return None
    text = ocr_text.strip()

    # 1. 精确命中
    if text in SHIKIGAMI_NAMES:
        return text

    # 2. 已知别名（形近字）
    if text in OCR_ALIAS:
        return OCR_ALIAS[text]

    # 3. 模糊匹配兜底：自动容错未预料到的形近字
    best, best_ratio = None, 0.0
    for name in SHIKIGAMI_NAMES:
        ratio = difflib.SequenceMatcher(None, text, name).ratio()
        if ratio > best_ratio:
            best, best_ratio = name, ratio
    if best is not None and best_ratio >= FUZZY_THRESHOLD:
        return best
    return None
