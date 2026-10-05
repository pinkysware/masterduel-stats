# -*- coding: utf-8 -*-
"""卡组名的模糊匹配 —— M5 的选择框用。

为什么需要：同一个卡组有多种叫法（英文名 / 显示名 / 中文名 / 别名，
甚至玩家口中的俗称）。用户打字时应该任何一种都能命中同一条。

匹配等级（**数字越小越优先**）：

====  ================  ==========================================
等级   情况              例
====  ================  ==========================================
0     完全相等          'pumpkingzombies' == 'pumpkingzombies'
1     前缀相等          'pump' -> Pumpking Zombies
2     子串包含          'zomb' -> Pumpking Zombies
3     近似（容错）      'pumpkinzombi'（拼错/漏字）
4     **字符集合包含**   'VSK9' -> K9VS、'圣耀' -> 烙印耀圣
      （换序/缩写）       —— 只要每个字符都够用，**不看顺序**
None  不匹配
====  ================  ==========================================

比较前先**归一化**：NFKC（全角→半角）、转小写、去掉空白与常见标点。
所以 `R.B.` / `r b` / `R\ufe63B` 都会被归一成 `rb`。
"""
import difflib
import re
import unicodedata
from collections import Counter

#: 近似匹配的门槛（difflib 相似度）
FUZZY_THRESHOLD = 0.72

#: 归一化时丢掉的字符：空白、连字符、点、各种括号、分隔符、引号、感叹问号
_PUNCT = re.compile(
    r'[\s\-_.·・、,，/\\|()（）\[\]【】{}<>《》\'"“”‘’!！?？+＋&＆:：;；~～]')


#: 走「字符集合包含」这级时，查询词至少要有这么多字符
#: （太短会到处命中，比如一两个字母）
MIN_ANAGRAM_LEN = 2


def _chars_covered(q, t):
    """q 的每个字符都能在 t 里找到（按出现次数算），**不看顺序**。

    'vsk9' vs 'k9vs'            -> True（同一组字符，只是换了序）
    'vsk9' vs 'vanquishsoulk9'  -> True（v/s/k/9 都在）
    '圣耀' vs '烙印耀圣'          -> True
    '珠泪' vs '泪冠哀歌'          -> False（没有「珠」）
    """
    if not q:
        return False
    cq, ct = Counter(q), Counter(t)
    return all(ct.get(ch, 0) >= n for ch, n in cq.items())


def normalize(s):
    """归一化：全角转半角、转小写、去掉空白与常见标点。"""
    if s is None:
        return ''
    s = unicodedata.normalize('NFKC', str(s))
    return _PUNCT.sub('', s.lower())


def match_score(query, text):
    """查询词与单个文本的匹配等级；不匹配返回 None。"""
    q, t = normalize(query), normalize(text)
    if not q or not t:
        return None
    if q == t:
        return 0
    if t.startswith(q):
        return 1
    if q in t:
        return 2
    if difflib.SequenceMatcher(None, q, t).ratio() >= FUZZY_THRESHOLD:
        return 3
    # 换序/缩写：difflib 是按顺序比的，'vsk9' 对 'k9vs' 只有 0.50，
    # 永远过不了 0.72。所以这里再兜一层「字符都够用」。
    if len(q) >= MIN_ANAGRAM_LEN and _chars_covered(q, t):
        return 4
    return None


def best_score(query, fields):
    """在多个字段里取最好的（最小的）匹配等级；全不中返回 None。"""
    best = None
    for f in fields or ():
        sc = match_score(query, f)
        if sc is not None and (best is None or sc < best):
            best = sc
    return best


def split_aliases(cell):
    """把 CSV 里的一格别名拆成列表。

    支持 ``/`` ``、`` ``,`` ``|`` ``;`` 作分隔（中英文都认），自动去空。
    """
    if not cell:
        return []
    parts = re.split(r'[/、,|;；]', str(cell))
    return [p.strip() for p in parts if p.strip()]
