# -*- coding: utf-8 -*-
"""拼音 / 首字母模糊匹配（**中文独有**，卡组选择器搜索用；2026-09-19 用户需求）。

用户原话：闪刀姬输入 ``sdj`` 或 ``shandaoji`` 都能查到 —— 前者是**首字母**、
后者是**全拼**；匹配口径 = 两个键的**子串**（大小写不敏感）。

三道闸（缺一不走拼音）：
1. **界面语言**：只有 ``lang == 'cn'`` 时选择器才调这里（用户拍板：切到其他语言
   关闭这个功能）——闸在 ``ui/selector._refill``；
2. **依赖**：``pypinyin``（纯 Python，MIT）没装 → :func:`available` False，
   功能整体关闭、UI 静默降级，绝不抛（装法：``pip install pypinyin``，官方源）；
3. **查询形态**：必须**纯英文字母**（``sdj`` / ``shandaoji``）——含汉字/数字/符号的
   查询走五字段直配（中文本来就搜得到，别抢它的活）。

键的口径：**只取汉字**的拼音 —— 英文名（K9vs）五字段匹配本来就能搜；日文汉字
（閃刀姫）读音不是汉语拼音，不掺。没有汉字的文本 → 空键，永不命中。
"""
from functools import lru_cache

try:
    from pypinyin import lazy_pinyin
    _HAS_PYPINYIN = True
except Exception:                                            # noqa: BLE001
    _HAS_PYPINYIN = False


def available() -> bool:
    """拼音功能可用吗（pypinyin 在不在）。不可用时 UI 静默降级为原五字段搜索。"""
    return _HAS_PYPINYIN


def is_pinyin_query(query) -> bool:
    """这个查询该不该走拼音：**纯英文字母**（``sdj`` / ``shandaoji``）才算。"""
    q = str(query or '')
    return bool(q) and q.isascii() and q.isalpha()


@lru_cache(maxsize=2048)
def keys(text) -> tuple:
    """文本的拼音键 -> ``(全拼, 首字母)``，小写、无分隔（含 lru_cache）。

    只取**汉字**的拼音：``errors='ignore'`` 丢掉非汉字片段（lazy_pinyin 对汉字
    逐字出一个 token，标点/英文不会混进键里）。喂进来的文本由调用方把关 ——
    别把日文名传进来（那些汉字的读音不是汉语拼音）。
    """
    if not _HAS_PYPINYIN or not text:
        return ('', '')
    try:
        #: ``errors='ignore'`` = 丢掉非汉字片段，只回汉字的拼音（逐字一个 token）
        py = lazy_pinyin(str(text), errors='ignore')
    except Exception:                                        # noqa: BLE001
        return ('', '')
    full, initial = [], []
    for token in py:
        t = (token or '').lower()
        if t.isascii() and t.isalpha():
            full.append(t)
            initial.append(t[0])
    return (''.join(full), ''.join(initial))


def match(query, *texts) -> bool:
    """query 是否命中任一 text 的拼音键（全拼或首字母的**子串**，大小写不敏感）。"""
    if not _HAS_PYPINYIN or not is_pinyin_query(query):
        return False
    q = str(query).lower()
    for t in texts:
        if not t:
            continue
        full, initial = keys(t)
        if q in full or q in initial:
            return True
    return False
