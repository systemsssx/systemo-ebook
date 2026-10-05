# -*- coding: utf-8 -*-
"""Book title/author normalization and candidate ranking.

Web-novel titles are messy: sources append promotional suffixes
(《赘婿（郭麒麟、宋轶主演影视剧同名原著）》), use full-width punctuation,
traditional characters (Taiwan/HK editions, Japanese sources) or extra
whitespace. Ranking must therefore compare on a normalized form, not raw text.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Iterable, Sequence

# --- titles that are "the same book, decorated" -----------------------------

# Bracketed decorations: （...） (...) 【...】 [...] 〔...〕
# NOTE: 《》 are deliberately NOT here. 《书名》 is the normal way to *write* a
# title, and treating it as decoration deletes the whole title. Book-title marks
# are unwrapped separately by _TITLE_MARK_RE.
_BRACKET_RE = re.compile(r"[（(【\[〔][^（()）【】\[\]〔〕]*[）)】\]〕]")
_TITLE_MARK_RE = re.compile(r"^《(?P<inner>.+)》$")

# Leading noise some sites put in front of the real title
_LEADING_NOISE_RE = re.compile(
    r"^\s*(?:完结|连载|免费|全本|小说|书名|作品|title)\s*[:：]?\s*", re.IGNORECASE
)
# Trailing volume/part markers: 第一部, 第2卷, 上卷, 下, 中
_VOLUME_TAIL_RE = re.compile(
    r"(?:第[一二三四五六七八九十百零〇\d]+[部卷册季集]|[（(]?[上中下][）)]?|[·\-—]\s*[一二三四五六七八九十\d]+)\s*$"
)
# A short subtitle glued on with a separator: 《十日终焉·囚笼》《魔道祖师：无羁》
# Publishers do this for volume/edition releases of the *same* work, which is why
# the base title still has to match. Long decorations are NOT folded, otherwise
# 《赘婿：我的八个姐姐风华绝代》 would collapse into 《赘婿》.
SHORT_TAIL_MAX = 3
_SHORT_TAIL_RE = re.compile(
    r"^(?P<base>.{2,}?)\s*[·・:：\-—–－]\s*(?P<tail>[^·・:：\-—–－\s]{1," + str(SHORT_TAIL_MAX) + r"})$"
)
# Punctuation and spacing
_PUNCT_RE = re.compile(r"[\s\u3000·・:：,，.。!！?？~～\-—_/\\|'\"“”‘’`^*#]+")

# Traditional -> simplified, best effort and dependency-free.
#
# A full conversion table is ~3000 entries; this covers the characters that
# actually appear in web-novel titles (HK/TW editions, Japanese listings that use
# traditional forms). Only unambiguous mappings are included — 乾/著/藉 and other
# one-to-many cases are deliberately absent, because a wrong mapping is worse
# than a missing one. Un-converted characters still fall back to fuzzy matching.
_TRAD_TO_SIMP = {
    # --- function words / pronouns / particles -------------------------------
    "這": "这", "們": "们", "個": "个", "麼": "么", "幾": "几", "兩": "两", "隻": "只",
    "誰": "谁", "嗎": "吗", "喲": "哟", "囉": "啰", "嘍": "喽", "唄": "呗", "與": "与",
    "為": "为", "無": "无", "會": "会", "來": "来", "從": "从", "於": "于", "還": "还",
    "對": "对", "過": "过", "讓": "让", "給": "给", "該": "该", "應": "应", "稱": "称",
    "將": "将", "並": "并", "卻": "却", "豈": "岂", "寧": "宁", "須": "须", "纔": "才",
    "儘": "尽", "盡": "尽", "復": "复", "複": "复", "係": "系", "繫": "系", "於": "于",
    # --- nouns: time, place, nature -----------------------------------------
    "時": "时", "間": "间", "現": "现", "實": "实", "際": "际", "舊": "旧", "後": "后",
    "週": "周", "歲": "岁", "萬": "万", "億": "亿", "冊": "册", "頁": "页", "節": "节",
    "國": "国", "縣": "县", "鄉": "乡", "鎮": "镇", "區": "区", "園": "园", "場": "场",
    "廣": "广", "廠": "厂", "樓": "楼", "層": "层", "廳": "厅", "廚": "厨", "廁": "厕",
    "門": "门", "閉": "闭", "開": "开", "關": "关", "鎖": "锁", "鑰": "钥", "牆": "墙",
    "磚": "砖", "燈": "灯", "陽": "阳", "陰": "阴", "風": "风", "雲": "云", "電": "电",
    "霧": "雾", "樹": "树", "葉": "叶", "藥": "药", "鳥": "鸟", "魚": "鱼", "龍": "龙",
    "鳳": "凤", "龜": "龟", "獸": "兽", "蟲": "虫", "鷹": "鹰", "鶴": "鹤", "鴨": "鸭",
    "雞": "鸡", "豬": "猪", "貓": "猫", "獅": "狮", "螞": "蚂", "蟻": "蚁", "鮮": "鲜",
    # --- body, life, medicine ------------------------------------------------
    "體": "体", "腦": "脑", "臉": "脸", "頭": "头", "髮": "发", "淚": "泪", "聲": "声",
    "膚": "肤", "膽": "胆", "腸": "肠", "臟": "脏", "髒": "脏", "脈": "脉", "腎": "肾",
    "頸": "颈", "項": "项", "領": "领", "醫": "医", "療": "疗", "癒": "愈", "瘋": "疯",
    "瘡": "疮", "癌": "癌", "釋": "释", "釀": "酿", "嚐": "尝", "嘗": "尝", "餓": "饿",
    "飽": "饱", "熱": "热", "涼": "凉", "濕": "湿", "淨": "净", "骯": "肮", "麵": "面",
    # --- war, weapons, magic -------------------------------------------------
    "戰": "战", "爭": "争", "軍": "军", "隊": "队", "陣": "阵", "敵": "敌", "劍": "剑",
    "槍": "枪", "彈": "弹", "師": "师", "帥": "帅", "勝": "胜", "敗": "败", "擊": "击",
    "殺": "杀", "傷": "伤", "護": "护", "衛": "卫", "謀": "谋", "計": "计", "詐": "诈",
    "騙": "骗", "賊": "贼", "盜": "盗", "搶": "抢", "奪": "夺", "獄": "狱", "罰": "罚",
    "靈": "灵", "煉": "炼", "燄": "焰", "熾": "炽", "滅": "灭", "咒": "咒", "詛": "诅",
    "禱": "祷", "聖": "圣", "禪": "禅", "俠": "侠", "義": "义", "氣": "气", "勢": "势",
    "訣": "诀", "譜": "谱", "術": "术", "寶": "宝", "圖": "图", "鑑": "鉴", "鑑": "鉴",
    # --- money, trade, governance -------------------------------------------
    "買": "买", "賣": "卖", "貨": "货", "貴": "贵", "賤": "贱", "賺": "赚", "賠": "赔",
    "費": "费", "資": "资", "財": "财", "產": "产", "業": "业", "銀": "银", "錢": "钱",
    "幣": "币", "債": "债", "稅": "税", "貸": "贷", "贈": "赠", "賞": "赏", "賜": "赐",
    "贏": "赢", "輸": "输", "負": "负", "責": "责", "賓": "宾", "贊": "赞", "賺": "赚",
    "鐵": "铁", "鋼": "钢", "錯": "错", "鎖": "锁", "鑄": "铸", "鐘": "钟", "鍾": "钟",
    "錶": "表", "銀": "银", "軟": "软", "較": "较", "載": "载", "輕": "轻", "輝": "辉",
    "車": "车", "輪": "轮", "轉": "转", "輛": "辆", "軌": "轨", "轄": "辖", "馬": "马",
    "駕": "驾", "駛": "驶", "驗": "验", "騎": "骑", "驅": "驱", "馳": "驰", "驚": "惊",
    "驕": "骄", "驟": "骤", "駱": "骆", "騰": "腾", "騷": "骚", "飛": "飞", "辦": "办",
    "辭": "辞", "遞": "递", "選": "选", "遺": "遗", "邊": "边", "隨": "随", "難": "难",
    "變": "变", "進": "进", "遠": "远", "運": "运", "連": "连", "達": "达", "適": "适",
    "過": "过", "邁": "迈", "遷": "迁", "邀": "邀", "邏": "逻", "輯": "辑", "農": "农",
    "釀": "酿", "趕": "赶", "趨": "趋", "跡": "迹", "踐": "践", "蹤": "踪", "軀": "躯",
    "迴": "回", "遞": "递", "鄰": "邻", "釐": "厘", "魯": "鲁", "鳴": "鸣", "麥": "麦",
    "齋": "斋", "齊": "齐", "齒": "齿", "黨": "党", "處": "处", "備": "备", "傑": "杰",
    # --- study, speech, thought ---------------------------------------------
    "學": "学", "書": "书", "畫": "画", "詩": "诗", "詞": "词", "課": "课", "習": "习",
    "練": "练", "級": "级", "別": "别", "種": "种", "類": "类", "數": "数", "統": "统",
    "網": "网", "線": "线", "織": "织", "結": "结", "紀": "纪", "絕": "绝", "續": "续",
    "繼": "继", "紅": "红", "綠": "绿", "藍": "蓝", "純": "纯", "紙": "纸", "筆": "笔",
    "鏡": "镜", "讀": "读", "寫": "写", "說": "说", "話": "话", "語": "语", "講": "讲",
    "認": "认", "識": "识", "記": "记", "訪": "访", "設": "设", "許": "许", "調": "调",
    "談": "谈", "請": "请", "論": "论", "謝": "谢", "譯": "译", "議": "议", "詢": "询",
    "訊": "讯", "號": "号", "確": "确", "觀": "观", "覺": "觉", "見": "见", "聽": "听",
    "聞": "闻", "聯": "联", "憶": "忆", "懷": "怀", "願": "愿", "夢": "梦", "緣": "缘",
    "戀": "恋", "愛": "爱", "憤": "愤", "憐": "怜", "憂": "忧", "歡": "欢", "樂": "乐",
    "懼": "惧", "慘": "惨", "靜": "静", "閒": "闲", "榮": "荣", "慶": "庆", "賀": "贺",
    "豐": "丰", "麗": "丽", "華": "华", "協": "协", "單": "单", "質": "质", "賽": "赛",
    "親": "亲", "裝": "装", "務": "务", "動": "动", "劇": "剧", "遊": "游", "戲": "戏",
    "鬥": "斗", "經": "经", "歷": "历", "傳": "传", "錄": "录", "標": "标", "樣": "样",
    "機": "机", "構": "构", "檔": "档", "屬": "属", "產": "产", "臨": "临", "舉": "举",
    "艙": "舱", "蓋": "盖", "軒": "轩", "輩": "辈", "閃": "闪", "鬧": "闹", "闖": "闯",
    "驅": "驱", "龐": "庞", "廢": "废", "廬": "庐", "廟": "庙", "庫": "库", "倉": "仓",
    # --- surnames (titles are full of them) ---------------------------------
    "陳": "陈", "張": "张", "劉": "刘", "楊": "杨", "黃": "黄", "趙": "赵", "吳": "吴",
    "孫": "孙", "羅": "罗", "鄭": "郑", "謝": "谢", "許": "许", "韓": "韩", "馮": "冯",
    "鄧": "邓", "蕭": "萧", "蔣": "蒋", "餘": "余", "蘇": "苏", "呂": "吕", "盧": "卢",
    "譚": "谭", "賈": "贾", "韋": "韦", "鄒": "邹", "閆": "闫", "賀": "贺", "顧": "顾",
    "龔": "龚", "嚴": "严", "賴": "赖", "陸": "陆", "葉": "叶", "鐘": "钟", "龍": "龙",
    "馬": "马", "錢": "钱", "孫": "孙", "喬": "乔", "萬": "万", "歐": "欧", "區": "区",
    # --- remainder of the high-frequency set ---------------------------------
    "裡": "里", "裏": "里", "長": "长", "點": "点", "發": "发", "兒": "儿", "順": "顺",
    "預": "预", "鴻": "鸿", "醜": "丑", "貝": "贝", "討": "讨", "總": "总", "響": "响",
    "嚮": "向", "麼": "么", "屬": "属", "雙": "双", "圖": "图", "團": "团", "圍": "围",
    "圓": "圆", "壞": "坏", "墳": "坟", "夢": "梦", "奧": "奥", "妝": "妆", "媽": "妈",
    "姐": "姐", "婦": "妇", "嬰": "婴", "孫": "孙", "學": "学", "寧": "宁", "寬": "宽",
    "寫": "写", "寶": "宝", "將": "将", "專": "专", "尋": "寻", "對": "对", "導": "导",
    "屆": "届", "嶺": "岭", "嶼": "屿", "幣": "币", "帶": "带", "幫": "帮", "幹": "干",
    "幾": "几", "廢": "废", "廳": "厅", "張": "张", "彈": "弹", "徑": "径", "徹": "彻",
    "態": "态", "憲": "宪", "戲": "戏", "戶": "户", "執": "执", "掃": "扫", "掛": "挂",
    "採": "采", "換": "换", "損": "损", "搖": "摇", "搶": "抢", "摻": "掺", "撐": "撑",
    "攝": "摄", "擺": "摆", "攔": "拦", "敵": "敌", "斃": "毙", "斷": "断", "曆": "历",
    "書": "书", "殺": "杀", "氣": "气", "決": "决", "淚": "泪", "測": "测", "渾": "浑",
    "溝": "沟", "滅": "灭", "潛": "潜", "濁": "浊", "濟": "济", "濤": "涛", "瀉": "泻",
    "災": "灾", "為": "为", "烏": "乌", "煙": "烟", "煩": "烦", "熱": "热", "營": "营",
    "爾": "尔", "牆": "墙", "犧": "牺", "狀": "状", "猶": "犹", "獨": "独", "獄": "狱",
    "獵": "猎", "獻": "献", "瑣": "琐", "環": "环", "現": "现", "瑪": "玛", "產": "产",
    "畢": "毕", "異": "异", "當": "当", "疇": "畴", "瘋": "疯", "瘍": "疡", "發": "发",
    "監": "监", "蓋": "盖", "盤": "盘", "眾": "众", "睜": "睁", "矚": "瞩", "礎": "础",
    "礙": "碍", "禮": "礼", "種": "种", "積": "积", "穩": "稳", "窮": "穷", "竄": "窜",
    "競": "竞", "筆": "笔", "節": "节", "範": "范", "築": "筑", "簡": "简", "籃": "篮",
    "籤": "签", "粵": "粤", "精": "精", "紀": "纪", "約": "约", "紅": "红", "純": "纯",
    "紙": "纸", "素": "素", "索": "索", "紫": "紫", "細": "细", "終": "终", "組": "组",
    "經": "经", "綁": "绑", "網": "网", "緊": "紧", "緒": "绪", "緣": "缘", "編": "编",
    "緩": "缓", "練": "练", "縱": "纵", "總": "总", "纖": "纤", "罷": "罢", "羅": "罗",
    "聖": "圣", "聞": "闻", "聯": "联", "聰": "聪", "職": "职", "聽": "听", "肅": "肃",
    "脅": "胁", "脈": "脉", "腦": "脑", "腳": "脚", "腫": "肿", "臉": "脸", "臨": "临",
    "舉": "举", "舊": "旧", "艙": "舱", "藝": "艺", "節": "节", "蘇": "苏", "蘭": "兰",
    "處": "处", "虛": "虚", "號": "号", "虧": "亏", "蟲": "虫", "衝": "冲", "補": "补",
    "裝": "装", "製": "制", "複": "复", "褲": "裤", "覺": "觉", "觀": "观", "計": "计",
    "訂": "订", "認": "认", "討": "讨", "讓": "让", "訓": "训", "記": "记", "訪": "访",
    "設": "设", "許": "许", "訴": "诉", "註": "注", "評": "评", "詞": "词", "試": "试",
    "詩": "诗", "話": "话", "詳": "详", "誇": "夸", "誌": "志", "認": "认", "語": "语",
    "誠": "诚", "誤": "误", "說": "说", "課": "课", "誼": "谊", "調": "调", "談": "谈",
    "請": "请", "論": "论", "諒": "谅", "諜": "谍", "講": "讲", "謝": "谢", "證": "证",
    "識": "识", "譜": "谱", "警": "警", "議": "议", "護": "护", "讀": "读", "變": "变",
    "讓": "让", "讚": "赞", "谷": "谷", "豐": "丰", "豬": "猪", "貝": "贝", "負": "负",
    "財": "财", "貢": "贡", "貧": "贫", "貨": "货", "販": "贩", "貪": "贪", "責": "责",
    "貴": "贵", "買": "买", "貸": "贷", "費": "费", "貼": "贴", "賀": "贺", "賊": "贼",
    "資": "资", "賈": "贾", "賓": "宾", "賣": "卖", "賤": "贱", "賦": "赋", "質": "质",
    "賺": "赚", "購": "购", "賽": "赛", "贏": "赢", "贛": "赣", "趙": "赵", "趕": "赶",
    "趨": "趋", "跡": "迹", "踐": "践", "躍": "跃", "躪": "躏", "身": "身", "車": "车",
    "軌": "轨", "軍": "军", "軒": "轩", "軟": "软", "較": "较", "載": "载", "輔": "辅",
    "輕": "轻", "輛": "辆", "輝": "辉", "輩": "辈", "輪": "轮", "輯": "辑", "輸": "输",
    "轄": "辖", "轉": "转", "轟": "轰", "辦": "办", "辭": "辞", "農": "农", "迴": "回",
    "遞": "递", "遠": "远", "選": "选", "遺": "遗", "邁": "迈", "還": "还", "邊": "边",
    "邏": "逻", "鄭": "郑", "鄰": "邻", "醫": "医", "醬": "酱", "釀": "酿", "釋": "释",
    "鐵": "铁", "鑄": "铸", "鑑": "鉴", "長": "长", "門": "门", "閃": "闪", "閉": "闭",
    "閏": "闰", "閑": "闲", "間": "间", "閘": "闸", "閣": "阁", "閱": "阅", "闆": "板",
    "闊": "阔", "隊": "队", "陽": "阳", "陰": "阴", "陣": "阵", "階": "阶", "際": "际",
    "隨": "随", "險": "险", "隱": "隐", "雖": "虽", "雙": "双", "雜": "杂", "雞": "鸡",
    "離": "离", "難": "难", "雲": "云", "電": "电", "霧": "雾", "靈": "灵", "靜": "静",
    "韓": "韩", "順": "顺", "須": "须", "預": "预", "頭": "头", "頹": "颓", "頻": "频",
    "顆": "颗", "題": "题", "顏": "颜", "願": "愿", "類": "类", "顧": "顾", "顫": "颤",
    "顯": "显", "風": "风", "飛": "飞", "飯": "饭", "飲": "饮", "飾": "饰", "養": "养",
    "餐": "餐", "餘": "余", "館": "馆", "馬": "马", "駕": "驾", "駛": "驶", "駐": "驻",
    "駭": "骇", "騎": "骑", "騰": "腾", "驅": "驱", "驗": "验", "驚": "惊", "骨": "骨",
    "體": "体", "高": "高", "髮": "发", "鬥": "斗", "鬼": "鬼", "魚": "鱼", "鮮": "鲜",
    "鳥": "鸟", "鳳": "凤", "鴻": "鸿", "鷹": "鹰", "鹽": "盐", "麗": "丽", "麥": "麦",
    "黃": "黄", "點": "点", "黨": "党", "鼓": "鼓", "齊": "齐", "齒": "齿", "龍": "龙",
    "龜": "龟",
}

# Identity pairs add nothing and only slow the per-character lookup down.
_TRAD_TO_SIMP = {k: v for k, v in _TRAD_TO_SIMP.items() if k != v}


def to_simplified(text: str) -> str:
    return "".join(_TRAD_TO_SIMP.get(ch, ch) for ch in text)


def clean_author(author: str | None) -> str:
    """Normalize an author string: drop roles, brackets, whitespace."""
    if not author:
        return ""
    text = unicodedata.normalize("NFKC", str(author))
    text = re.sub(r"[\[【(（][^\]】)）]*[\]】)）]", " ", text)
    text = re.sub(r"(著|作品|作品集|原著|編|编|译|譯|著者)$", "", text.strip())
    text = re.sub(r"\s*(等|著|作品)\s*$", "", text)
    return re.sub(r"\s+", " ", text).strip(" ·,，、")


def _fold_short_tail(text: str) -> str:
    """Drop a short glued-on subtitle: '十日终焉·囚笼' -> '十日终焉'."""
    match = _SHORT_TAIL_RE.match(text.strip())
    return match.group("base").strip() if match else text


def norm_title(title: str | None, *, strip_brackets: bool = True,
               fold_tail: bool = False) -> str:
    """Canonical comparison form of a title.

    `fold_tail=True` additionally removes a short `分隔符+副标题` suffix before
    punctuation is dropped (the punctuation would otherwise erase the separator
    and make the two forms indistinguishable).
    """
    if not title:
        return ""
    text = unicodedata.normalize("NFKC", str(title)).strip()
    for _ in range(2):
        marked = _TITLE_MARK_RE.match(text)
        if not marked:
            break
        text = marked.group("inner").strip()
    if strip_brackets:
        # Repeat: nested decorations like 《书名（番外）》
        for _ in range(3):
            new = _BRACKET_RE.sub("", text)
            if new == text:
                break
            text = new
    text = _LEADING_NOISE_RE.sub("", text)
    for _ in range(2):
        new = _VOLUME_TAIL_RE.sub("", text)
        if new == text:
            break
        text = new
    if fold_tail:
        text = _fold_short_tail(text)
    text = to_simplified(text)
    text = _PUNCT_RE.sub("", text)
    return text.lower()


def search_title(title: str | None) -> str:
    """Send-to-source form of a title: strip only the *decorative* parts.

    Sources are literal search engines. 《赘婿（郭麒麟…主演影视剧同名原著）》 and
    「败犬女主太多了 第八卷」 return nothing from weread/jjwxc (measured), while the
    bare title works. `norm_title()` is for *comparison* — it also drops punctuation
    and lowercases, which is too aggressive to hand to a search box. This keeps the
    readable form so the source's own ranking still has something to match on.
    """
    if not title:
        return ""
    text = unicodedata.normalize("NFKC", str(title)).strip()
    for _ in range(2):
        marked = _TITLE_MARK_RE.match(text)
        if not marked:
            break
        text = marked.group("inner").strip()
    for _ in range(3):
        new = _BRACKET_RE.sub("", text)
        if new == text:
            break
        text = new
    text = _LEADING_NOISE_RE.sub("", text)
    for _ in range(2):
        new = _VOLUME_TAIL_RE.sub("", text)
        if new == text:
            break
        text = new
    cleaned = text.strip().strip("·・-—–－:：,，、;；")
    return cleaned or str(title).strip()


def title_similarity(query: str, candidate: str) -> float:
    """0..1 closeness between the queried title and a candidate title.

    Score bands (used by the ranking and by the "is this a confident match?"
    check):
        1.00  identical after normalization
        0.97  identical once a short volume/edition subtitle is folded away
              (still below a true exact match, so it never steals a lookup from
              the plain title when both exist)
        0.90+ candidate extends the query ("赘婿" vs "赘婿当道")
        0.72+ one contains the other
        else  scaled fuzzy ratio
    """
    q, c = norm_title(query), norm_title(candidate)
    if not q or not c:
        return 0.0
    if q == c:
        return 1.0
    qf, cf = norm_title(query, fold_tail=True), norm_title(candidate, fold_tail=True)
    folded = (qf != q) or (cf != c)
    if folded and (qf == cf or cf == q or qf == c):
        # One side (or both) carries a short subtitle: same work, decorated.
        return 0.97
    if c.startswith(q) or q.startswith(c):
        shorter, longer = sorted((q, c), key=len)
        # "赘婿" vs "赘婿的日常" is closer than "赘婿" vs "赘婿外传铁血江湖"
        return 0.90 + 0.06 * (len(shorter) / len(longer))
    if q in c or c in q:
        shorter, longer = sorted((q, c), key=len)
        return 0.72 + 0.16 * (len(shorter) / len(longer))
    ratio = SequenceMatcher(None, q, c).ratio()
    return max(0.0, (ratio - 0.45) / 0.55) * 0.70


def author_similarity(query: str, candidate: str) -> float:
    q, c = norm_title(query, strip_brackets=False), norm_title(candidate, strip_brackets=False)
    if not q or not c:
        return 0.0
    if q == c:
        return 1.0
    if q in c or c in q:
        return 0.8
    return SequenceMatcher(None, q, c).ratio()


@dataclass
class Candidate:
    """One book record returned by a source."""

    title: str
    author: str = ""
    cover: str = ""
    source: str = ""
    url: str = ""
    intro: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0
    title_score: float = 0.0
    source_rank: int = 0
    position: int = 0  # index in the source's own result list (relevance order)

    def as_dict(self, *, with_extra: bool = False) -> dict[str, Any]:
        out = {
            "title": self.title,
            "author": self.author,
            "cover": self.cover,
            "source": self.source,
        }
        if self.url:
            out["url"] = self.url
        if self.intro:
            out["intro"] = self.intro
        out["score"] = round(self.score, 4)
        if with_extra and self.extra:
            out["extra"] = self.extra
        return out


# ★ 2026-10-01：同名同人簇阈值（见 rank_candidates 里那段说明）
_FANFIC_CLUSTER_MIN = 3          # ≥3 条"查询词 + 更多字"的前缀派生 → 判定被大量同人化
_FANFIC_DEMOTE_TO = 0.90         # 同人嫌疑的光名同名候选压到这个分（低于 AUTO_MIN=0.95）
_TRUSTED_FOR_EXACT = {           # 这两个源上的"书名精确同名"可信，不算同人嫌疑
    "bilinovel",                 #   哔哩轻小说（正版轻小说站，且站内搜索带别名栏）
    "qidiantracker",             #   起点榜快照（起点官方榜，MIT 社区镜像）
}


def _bare_text(s: str) -> str:
    """只留文字/数字，去掉所有标点空格。用于判断"书名是不是光秃秃的同名"。

    ★ 为什么不能用 norm_title：它会把**括号里的副标题折掉** ——
      norm_title('赘婿（郭麒麟 、宋轶主演影视剧同名原著）') 会折成 '赘婿'，
      于是那本**正经的官方原著**会被误判成"光名同名"而同人嫌疑（实测我第一版就这么误伤了）。
      这里要求去掉标点后**一字不差**：《赘婿（郭麒麟…）》→ '赘婿郭麒麟宋轶主演影视剧同名原著' ≠ '赘婿'，
      自然不算光名；而《无职转生》→ '无职转生' == 查询词，才算。
    """
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", s or "")


def rank_candidates(
    query_title: str,
    candidates: Iterable[Candidate],
    *,
    author_hint: str = "",
    source_priority: Sequence[str] = (),
) -> list[Candidate]:
    """Score and sort candidates; best first. Mutates and returns the list.

    Ties are broken by *the source's own result order* before title length: when
    several books share a title exactly (e.g. WeRead returns three different
    books literally called 《赘婿》), the platform's ranking already encodes
    relevance and popularity, which is a far better signal than "shortest title
    first". This is what makes 《赘婿》 resolve to 愤怒的香蕉 rather than to an
    obscure namesake.
    """
    priority = {name: i for i, name in enumerate(source_priority)}
    cands = list(candidates)
    nq = norm_title(query_title)

    # ★★★ 2026-10-01：**同名同人簇**判定（实测《无职转生》踩出来的）★★★
    #   现象：查「无职转生」，晋江上一本**书名一字不差**的同人（幼稚园校霸）拿到
    #   title_score=1.0 直接夺魁，而正主《无职转生 : 到了异世界就拿出真本事2》
    #   （不讲理不求人＝「理不尽な孫の手」的直译，微信读书）只有 0.9150、排第 7。
    #   为什么纯书名相似度救不了：**在 UGC 平台上，"书名抄得一模一样"恰恰是同人最常见的起名法**。
    #   判据（三条同时成立才动手，实测不误伤别的书）：
    #     ① 候选池里有 ≥3 条"查询词 + 更多字"的**前缀派生** → 这部作品被大量同人化；
    #     ② 某候选的书名**恰好等于**查询词（光秃秃的同名）；
    #     ③ 它**不是权威源**（哔哩轻小说 / 起点榜快照）—— 那两个源的同名可信。
    #   处理：**不硬选**，只把它的 title_score 压到 AUTO_MIN(0.95) 以下 ——
    #   meta_lookup 于是判为"不够硬"，返回 ambiguous=true + 候选列表，
    #   界面弹【同名结果选择窗】让用户自己点（用户一直要的就是这个）。
    #   反例核对：①《赘婿》虽也有同人簇，但夺魁的是《赘婿（郭麒麟、宋轶主演影视剧同名原著）》
    #   ——带副标题、不是光名，②③不成立 → 规则不触发，行为与以前一致。
    n_deriv = 0
    if nq:
        for _c in cands:
            _nc = norm_title(_c.title)
            if _nc and _nc != nq and _nc.startswith(nq):
                n_deriv += 1
    fanfic_suspect = n_deriv >= _FANFIC_CLUSTER_MIN

    result: list[Candidate] = []
    for cand in cands:
        cand.author = clean_author(cand.author)
        cand.title_score = title_similarity(query_title, cand.title)
        # ★ 2026-09-29 译名容错：源已经把「查询词真实出现在目标页面原文里」当证据时
        #   （FanqieSource/BilinovelSource 的 _search_via_bing 会标 extra['matchedBy']='content'），
        #   不要再用书名相似度把它压下去 ——
        #   实测《败犬女主太多了》在哔哩轻小说的正名是《败北女角太多了！》，相似度只有 0.34，
        #   而番茄上有一本照抄官方书名的同人（相似度 1.00）→ 不抬这一手就会选错书。
        # ★ 2026-09-30 追加 'site'：哔哩轻小说**站内搜索**给出"单本精确命中"页时
        #   （页面里自带「别名」栏，实测「败犬女主太多了」→ 3095），站点自己已经认定
        #   是同一部作品 —— 证据比"页面里出现过查询词"更硬，同样抬到 0.99。
        if cand.extra.get("matchedBy") in ("content", "site"):
            cand.title_score = max(cand.title_score, 0.99)
        elif (fanfic_suspect and nq and _bare_text(cand.title) == _bare_text(query_title)
              and cand.source not in _TRUSTED_FOR_EXACT):
            # 光名同名 + 同人簇 + 非权威源 → 同人嫌疑（见上）
            cand.extra["fanficSuspect"] = True
            cand.title_score = min(cand.title_score, _FANFIC_DEMOTE_TO)
        score = cand.title_score
        if author_hint and cand.author:
            score += 0.45 * author_similarity(author_hint, cand.author) * (
                1.0 if cand.title_score >= 0.9 else 0.5
            )
        # A record with a cover beats an otherwise identical record without one.
        if cand.cover:
            score += 0.04
        if cand.author:
            score += 0.02
        score -= 0.01 * priority.get(cand.source, len(priority))
        cand.score = round(score, 6)
        cand.source_rank = priority.get(cand.source, len(priority))
        result.append(cand)
    result.sort(key=lambda c: (-c.score, c.source_rank, c.position, len(c.title)))
    return result


def is_confident(candidate: Candidate | None, threshold: float = 0.85) -> bool:
    return bool(candidate and candidate.title_score >= threshold)
