#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""游戏版本更新前后自检 —— 「更新会不会把我们读内存的那条链打断」。

## 为什么要这个工具

我们读游戏内存的**每一个偏移都是从游戏里现查的**（见 `core/memread/reader_base.py`
顶部那条红线：绝不硬编码）。但「现查」有两个前提，一次 Unity / 游戏版本更新
就可能把它们打掉：

1. `global-metadata.dat` 的**头部结构**不变（我们靠 `magic + version + 两个偏移`
   这 16 字节在进程里认出那份镜像）；
2. `Il2CppClass` 的**结构体布局**不变（`core/memread/il2cpp.py` 里那串
   `TYPEINFO_*_OFFSET` 是按 **v31** 实机校准的 —— 布局一变，`list_fields()`
   读出来的字段表就是垃圾，表现为「偏移查得到但读写全错」）。

它俩坏了之后的**症状极其安静**：`tools/duel_collector.py` 的 `Runtime.attach()`
把所有异常吞掉只返回 False，主循环只会打「…等待游戏就绪（第 N 次）」——
和「游戏还没开完」**长得一模一样**。所以更新后最需要的不是修，而是**先分清是哪一段断了**。

## 三种模式

    # ① 更新前存基线（一定要在更新前跑，且游戏在跑 —— 否则拿不到在线部分）
    python tools/check_game_update.py --snapshot

    # ② 更新后离线比对（**不用开游戏**，几毫秒）—— 只比文件与名字表
    python tools/check_game_update.py --offline

    # ③ 更新后在线深检（**要开游戏，最好进一场对局**）—— 真去定位 TypeInfo / 字段
    python tools/check_game_update.py --live

`--offline` 能立刻回答三个问题：metadata 版本变了吗？我们依赖的类名/字段名还在吗？
Unity 版本变了吗？`--live` 才回答「TypeInfo 还定位得到吗、字段偏移还读得出来吗」。

## 依赖清单从哪来

**从代码里现推，不在这儿抄第二份**（抄了就会与实现漂移，这是本项目踩过的坑）：

* 类名：扫 `core/memread/*.py` 与 `tools/duel_collector.py` 里 `find_typeinfo(...)` 的
  字面量参数 + 各模块的 `CLASS_NAME` / `VC_CLASS` 常量；
* 字段名：各 Reader 类的白名单常量（`ENGINE_FIELDS` / `DUELINFO_FIELDS` / …）；
* 文件位置：`core.memread.il2cpp.METADATA_RELPATH`。

全程只读：只读 metadata 文件 + `open_game_file` 的 `rb`；在线模式只申请
`PROCESS_VM_READ | PROCESS_QUERY_INFORMATION`（`core/memread` 的既有约束）。
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.memread.end_message import default_game_dir                 # noqa: E402
from core.memread.il2cpp import (METADATA_MAGIC, TYPEINFO_FIELDS_OFFSET,  # noqa: E402
                                 TYPEINFO_NAME_OFFSET, TYPEINFO_NS_OFFSET,
                                 TYPEINFO_STATIC_FIELDS_OFFSET,
                                 TYPEINFO_TYPEDEF_OFFSET,
                                 MetadataHeader, default_metadata_path,
                                 read_string_table)

#: 基线文件（本机数据，**不入库**：每台机器的游戏目录/版本都不同）
DEFAULT_BASELINE = os.path.join(ROOT, 'data', 'game_update_baseline.json')

#: 依赖的**类**（namespace, class）。带 `None` 表示「namespace 未知、按名字搜」。
#: ⚠️ 这张表是**兜底**：`collect_dependencies()` 会先从代码里推，
#:    推出来的并进这里 —— 新加的 Reader 不用回来改这个文件。
KNOWN_CLASSES = (
    ('YgomGame.Duel', 'Engine'),
    ('YgomGame.Duel', 'DuelClient'),
    ('YgomGame.Duel', 'DuelLogController'),
    ('YgomGame.Duel', 'DuelEndMessage'),
    ('YgomSystem.Utility', 'ClientWork'),
    ('YgomGame.Menu', 'PresentBoxViewController'),
    (None, 'DeckEditViewController2'),
)

#: 依赖的**字段名**（不区分属于哪个类 —— metadata 字符串表是全局的，
#: 这里只回答「这个名字还在不在表里」。具体归属由 `--live` 的字段表校验）。
#: 同样是兜底，`collect_dependencies()` 会从 Reader 的白名单常量里补。
KNOWN_FIELDS = (
    # 对局状态（duel_state）
    'threadWork', 'currentEngineData', 'duelInfo', 'nTurnNum', 'nCurrentPhase',
    'bWhichTurnNow', 'nMovablePhase', 'nCurrentStep', 'nCurrentDmgStep',
    # LP（duel_state）
    'duelHUD', 'nearLPCounter', 'farLPCounter', 'm_LPCurrent',
    'm_LPBeforeTrans',
    # 公开卡（public_cards）
    'uidTbl', 'uidBases', 'cardAttribute',
    # 动作流（duel_log）
    'm_LogBaseItemList', 'm_DataList_ShowAction', 'm_Host',
    # 结算文案（end_message）
    'player0Name', 'player1Name', 'messageText', 'nextButtonText',
    # 对局设置（duel_settings）
    'engineInitializer', 'duelSettings',
    # 卡组编辑（deck_edit）
    'main', 'extra',
    # 礼盒（present_box）
    'pID', 'itemCategory', 'itemID', 'quantity',
    # 账号 / 段位（rank / client_work）
    's_data', 'User', 'profile', 'name', 'rank', 'rate',
    # 结算 / 巅峰（duel_result / peak_rate）
    'DuelResult', 'resultInfo', 'RateDuel', 'old_rate', 'new_rate',
    # 采集器（duel_collector）
    'gameMode', 'pvpFinished',
)


#: 主界面下允许「未就绪」的类 —— 对局/结算/礼盒/编辑界面才触发懒加载。
#: 2026-10-30 实测：这几个类 fields 指针为 NULL（甚至 TypeInfo 未构造）
#: 是 IL2CPP 懒加载，不是布局坏；别把 --live 判红。
LAZY_ON_IDLE = ('DeckEditViewController2', 'PresentBoxViewController',
                'DuelLogController', 'DuelEndMessage')


# ---------------------------------------------------------------------- 依赖收集

def _iter_py(path_dir):
    for dp, _sub, files in os.walk(path_dir):
        for fn in files:
            if fn.endswith('.py'):
                yield os.path.join(dp, fn)


def collect_dependencies():
    """从代码里推「我们依赖哪些类名 / 字段名」—— **唯一来源，别在本文件抄第二份**。

    扫两处：`core/memread/*.py`（Reader 与布局）与 `tools/duel_collector.py`
    （采集器的 Engine / DuelClient / 控制器定位 + 两个字段）。

    :return: ``(classes, fields, notes)`` —— classes/fields 是 set，
        notes 是「从哪推出来的」说明（打印给人看，证明不是硬编码）。
    """
    classes = set(KNOWN_CLASSES)
    fields = set(KNOWN_FIELDS)
    notes = []

    # ① find_typeinfo(ns, name) 的字面量参数（AST 解析，比正则稳）
    for p in list(_iter_py(os.path.join(ROOT, 'core', 'memread'))) + [
            os.path.join(ROOT, 'tools', 'duel_collector.py')]:
        try:
            with open(p, encoding='utf-8') as fh:
                tree = ast.parse(fh.read(), filename=p)
        except (OSError, SyntaxError):
            continue
        rel = os.path.relpath(p, ROOT).replace('\\', '/')
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(
                fn, 'id', None)
            if name != 'find_typeinfo':
                continue
            lit = [a.value for a in node.args
                   if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            # 本模块 wrapper（deck_edit 自带签名）只有一个类名
            if len(lit) >= 2:
                classes.add((lit[-2], lit[-1]))
            elif len(lit) == 1:
                classes.add((None, lit[0]))

    # ② 各模块的类名常量
    for rel, const in (('core/memread/deck_edit.py', 'CLASS_NAME'),
                       ('core/memread/present_box.py', 'VC_CLASS')):
        val = _module_const(rel, const)
        if val:
            classes.add((None, val))
            notes.append('%s: %s = %r' % (rel, const, val))

    # ③ 字段白名单常量（`('a','b')` 形式的模块级/类级常量）
    want = ('ENGINE_FIELDS', 'THREADWORK_FIELDS', 'CED_FIELDS',
            'DUELINFO_FIELDS', 'DUELCLIENT_FIELDS', 'DUELHUD_FIELDS',
            'LP_FIELDS', 'CONTROLLER_FIELDS', 'LIST_FIELDS', 'TEXT_FIELDS',
            'REQUIRED_FIELDS', '_REQUIRED_FIELDS', 'INITIALIZER_FIELDS',
            'PROFILE_KEYS', 'PROFILE_PATH')
    for p in _iter_py(os.path.join(ROOT, 'core', 'memread')):
        try:
            with open(p, encoding='utf-8') as fh:
                tree = ast.parse(fh.read(), filename=p)
        except (OSError, SyntaxError):
            continue
        rel = os.path.relpath(p, ROOT).replace('\\', '/')
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [
                node.target]
            value = node.value
            if not isinstance(value, (ast.Tuple, ast.List)):
                continue
            got = [e.value for e in value.elts
                   if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            if not got:
                continue
            for t in targets:
                tname = getattr(t, 'id', None) or getattr(t, 'attr', None)
                if tname in want:
                    fields.update(got)
                    notes.append('%s: %s -> %d 项' % (rel, tname, len(got)))
                    break

    return classes, fields, notes


def _module_const(rel, const):
    """读某个模块级的字符串常量（AST，不 import —— 避免拉起 Qt / 联网）。"""
    try:
        with open(os.path.join(ROOT, rel), encoding='utf-8') as fh:
            tree = ast.parse(fh.read())
    except (OSError, SyntaxError):
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if getattr(t, 'id', None) == const and isinstance(
                        node.value, ast.Constant):
                    return node.value.value
    return None


# ---------------------------------------------------------------------- 离线检查

def read_metadata_fingerprint(game_dir):
    """metadata 文件的**版本指纹**（不开游戏也能算）。

    :return: dict；文件不存在给 ``{'exists': False}``
    """
    path = default_metadata_path(game_dir)
    out = {'exists': os.path.exists(path), 'path': path}
    if not out['exists']:
        return out
    try:
        out['size'] = os.path.getsize(path)
        out['mtime'] = round(os.path.getmtime(path), 0)
        header = MetadataHeader.from_file(path)
        out.update({
            'magic': '%08X' % header.get('magic'),
            'version': header.version,
            'string_offset': header.string_offset,
            'string_size': header.string_size,
            'valid': bool(header.is_valid),
        })
    except Exception as e:                                   # noqa: BLE001
        out['error'] = repr(e)
    return out


def read_unity_version(game_dir):
    """Unity 版本号 —— 从 `data.unity3d` 的 UnityFS 包头里读（纯文件，不联网）。

    包头形如 ``UnityFS\\0\\0\\0\\0\\x08 5.x.x\\0 6000.0.61f1\\0``：字段 4 = 引擎版本。
    ⚠️ 读不到就给 None（**不猜**）—— 版本号只用于「变了没有」的提示，不是判据。
    """
    from core.memread.process import open_game_file
    path = os.path.join(game_dir, 'masterduel_Data', 'data.unity3d')
    try:
        # 走 open_game_file：游戏目录里的文件只许 'rb'
        # （红线在 core/memread/redline.py）
        with open_game_file(path, 'rb') as fh:
            head = fh.read(128)
    except (OSError, ValueError):
        return None
    if not head.startswith(b'UnityFS'):
        return None
    # 包头布局：[0] 格式版本(5.x.x，**不是**引擎版本) [1] 引擎版本(6000.0.61f1)
    parts = [p.strip(b'\x08').decode('utf-8', 'replace').strip()
             for p in head.split(b'\x00')]
    for text in parts:
        # 引擎版本形如 6000.0.61f1 / 2022.3.12f1：末段带 f 或全数字。
        # '5.x.x' 这种带字面 x 的是**文件格式版本**，跳过。
        if text.count('.') < 2 or not text[0].isdigit() or 'x' in text.lower():
            continue
        if 'f' in text.lower() or text.replace('.', '').isdigit():
            return text
    return None


def read_file_sizes(game_dir):
    """几个关键游戏文件的字节数 —— 变了就说明客户端被换过。"""
    out = {}
    for rel in ('GameAssembly.dll', 'masterduel.exe',
                os.path.join('masterduel_Data', 'data.unity3d'),
                os.path.join('masterduel_Data', 'il2cpp_data', 'Metadata',
                             'global-metadata.dat')):
        p = os.path.join(game_dir, rel)
        try:
            out[rel.replace('\\', '/')] = os.path.getsize(p)
        except OSError:
            out[rel.replace('\\', '/')] = None
    return out


def check_names(table, classes, fields):
    """名字还在不在 metadata 字符串表里（**离线**，纯字节查找）。

    判据与 `core.memread.il2cpp.find_string_offset` 同口径：字符串表是一串
    NUL 结尾的条目，所以查 ``\\0name\\0``（表首条目除外）。
    """
    def has(name):
        raw = name.encode('utf-8')
        return (b'\x00' + raw + b'\x00') in table or table.startswith(
            raw + b'\x00')

    miss_cls = sorted(n for _ns, n in classes if not has(n))
    miss_ns = sorted({ns for ns, _n in classes if ns and not has(ns)})
    miss_fld = sorted(n for n in fields if not has(n))
    return {'missing_classes': miss_cls, 'missing_namespaces': miss_ns,
            'missing_fields': miss_fld}


def snapshot(game_dir):
    """更新前/后都跑这一份 —— 输出可直接 json.dump 成基线。"""
    classes, fields, notes = collect_dependencies()
    meta = read_metadata_fingerprint(game_dir)
    out = {
        'game_dir': game_dir,
        'unity_version': read_unity_version(game_dir),
        'metadata': meta,
        'file_sizes': read_file_sizes(game_dir),
        'dep_classes': sorted('%s.%s' % (ns, n) if ns else n
                              for ns, n in classes),
        'dep_fields': sorted(fields),
        'dep_notes': notes,
    }
    if meta.get('exists') and not meta.get('error'):
        try:
            header = MetadataHeader.from_file(meta['path'])
            table = read_string_table(meta['path'], header)
            out['names'] = check_names(table, classes, fields)
        except Exception as e:                               # noqa: BLE001
            out['names'] = {'error': repr(e)}
    return out


def diff(before, after):
    """两份快照的差异 -> [(严重度, 一句人话)]。

    严重度：``BAD`` 会直接打断采集；``WARN`` 需要人工确认；``INFO`` 只是提示。
    """
    out = []

    bm, am = before.get('metadata') or {}, after.get('metadata') or {}
    if bm.get('version') != am.get('version'):
        out.append(('BAD', 'metadata 版本变了：v%s -> v%s —— '
                    '`core/memread/il2cpp.py` 的 Il2CppClass 布局常量是按 v31 '
                    '校准的，**必须实机重新校准**（见 docs/游戏更新应对计划.md）'
                    % (bm.get('version'), am.get('version'))))
    if bm.get('string_size') != am.get('string_size'):
        out.append(('INFO', '字符串表大小 %s -> %s（新增/删除类或字段的正常现象）'
                    % (bm.get('string_size'), am.get('string_size'))))
    if bm.get('valid') and not am.get('valid'):
        out.append(('BAD', 'metadata 头部不再合法（magic/version/偏移有一项不对）'
                    '—— find_metadata_mirror 会直接抛 ValueError'))

    if before.get('unity_version') != after.get('unity_version'):
        out.append(('WARN', 'Unity 版本变了：%s -> %s —— IL2CPP 运行时版本可能随之变化'
                    % (before.get('unity_version'), after.get('unity_version'))))

    bn = before.get('names') or {}
    an = after.get('names') or {}
    for key, what in (('missing_classes', '类名'),
                      ('missing_namespaces', '命名空间'),
                      ('missing_fields', '字段名')):
        gone = sorted(set(an.get(key) or []) - set(bn.get(key) or []))
        if gone:
            out.append(('BAD', '%s 在新 metadata 里找不到了：%s '
                        '—— 定位/读取这条链直接断，要按新名字改代码'
                        % (what, '、'.join(gone))))
        back = sorted(set(bn.get(key) or []) - set(an.get(key) or []))
        if back:
            out.append(('INFO', '%s 又出现了：%s' % (what, '、'.join(back))))

    bf, af = before.get('file_sizes') or {}, after.get('file_sizes') or {}
    for rel in sorted(set(bf) | set(af)):
        if bf.get(rel) != af.get(rel):
            out.append(('INFO', '%s 大小 %s -> %s'
                        % (rel, bf.get(rel), af.get(rel))))
    return out


# ---------------------------------------------------------------------- 在线检查

def live_report(game_dir):
    """真进程深检：镜像 -> 每个类的 TypeInfo -> 字段表。

    **不写内存、不调用游戏函数**（与其他 memread 工具同一条只读约束）。
    :return: ``(ok, lines)`` —— ok=False 表示这条链已经断了
    """
    from core.memread.il2cpp import (find_all_metadata_mirrors, find_typeinfo,
                                     list_fields)
    from core.memread.process import SafeProcess, find_pids_by_name

    lines = []
    pids = find_pids_by_name('masterduel.exe')
    if not pids:
        return False, ['游戏没在运行（masterduel.exe 未找到）—— 先启动游戏再跑 --live']
    lines.append('进程 pid=%s' % pids[0])

    meta = default_metadata_path(game_dir)
    try:
        header = MetadataHeader.from_file(meta)
    except Exception as e:                                   # noqa: BLE001
        return False, lines + ['读不到 metadata 文件：%r' % e]
    if not header.is_valid:
        return False, lines + ['metadata 头部不合法：%r' % header]
    lines.append('磁盘 metadata：%r' % header)
    try:
        table = read_string_table(meta, header)
    except Exception as e:                                   # noqa: BLE001
        return False, lines + ['读不出字符串表：%r' % e]

    ok = True
    proc = None
    try:
        proc = SafeProcess(pids[0])
        mirrors = find_all_metadata_mirrors(proc, header)
        if not mirrors:
            return False, lines + [
                '❌ 进程里找不到 metadata 镜像 —— 这条链的第一步就断了。'
                ' 可能原因：游戏还在加载 / metadata 不再整份读进内存 / '
                '头部特征对不上（magic 或版本变了）']
        lines.append('✅ metadata 镜像 %d 份，基址 %s'
                     % (len(mirrors), '、'.join(hex(m.base) for m in mirrors[:3])))
        mirror = mirrors[0]

        classes, fields, _notes = collect_dependencies()
        for ns, name in sorted(classes, key=lambda x: (x[0] or '', x[1])):
            label = ('%s.%s' % (ns, name)) if ns else name
            if ns:
                tis = find_typeinfo(proc, mirror, table, ns, name)
            else:
                # namespace 未知：按名字 + 逐个已知 ns 试
                tis = []
                for cand_ns in ('YgomGame.Duel', 'YgomGame.Menu',
                                'YgomGame.Deck', 'YgomSystem.Utility'):
                    tis = find_typeinfo(proc, mirror, table, cand_ns, name)
                    if tis:
                        break
            if not tis:
                if name in LAZY_ON_IDLE:
                    lines.append('💤 %s：TypeInfo 还没构造（懒加载 —— '
                                 '进对应界面后自然就绪）' % label)
                else:
                    ok = False
                    lines.append('❌ %s：定位不到 TypeInfo' % label)
                continue
            ti = tis[0]
            try:
                fptr = proc.read_ptr(ti + TYPEINFO_FIELDS_OFFSET)
            except Exception:                                # noqa: BLE001
                fptr = 0
            if not fptr and name in LAZY_ON_IDLE:
                lines.append('💤 %s：字段表懒加载未触发（fields 指针为空）'
                             ' —— 进对局/开对应界面后自然就绪' % label)
                continue
            try:
                got = list_fields(proc, ti, mirror=mirror)
            except Exception as e:                           # noqa: BLE001
                ok = False
                lines.append('❌ %s：TypeInfo 拿到了但字段表读不出（%r）'
                             ' —— 典型的 **Il2CppClass 布局变了**' % (label, e))
                continue
            # 布局自检：typeDefinition 必须指向 metadata 镜像
            try:
                td = proc.read_ptr(ti + TYPEINFO_TYPEDEF_OFFSET)
                lo, hi = mirror.base, mirror.base + header.get('string_size', 0) \
                    + header.string_offset + 0x1000000
                in_mirror = lo <= td < hi
            except Exception:                                # noqa: BLE001
                in_mirror = False
            mark = '✅' if (got and in_mirror) else '⚠️'
            lines.append('%s %s：TypeInfo=%s，字段 %d 个，typeDefinition %s'
                         % (mark, label, hex(ti), len(got),
                            '落在镜像内' if in_mirror else '**不在镜像内（布局可疑）**'))
            if not got:
                ok = False
            elif not in_mirror:
                ok = False
            else:
                # 字段名抽查：类里应该能看到我们依赖的某个名字
                names = set()
                for f in got:
                    names.add(f.name)
                    # 自动属性的字段名带包装：<duelHUD>k__BackingField
                    if f.name.startswith('<') and f.name.endswith(
                            'k__BackingField'):
                        names.add(f.name[1:-len('k__BackingField') - 1])
                want = _expected_fields_for(name)
                got_want = sorted(want & names)
                if want and not got_want:
                    lines.append('   ⚠️ 该类的字段表里没有预期字段 %s —— '
                                 '字段可能改名（实际有：%s）'
                                 % ('/'.join(sorted(want)),
                                    ', '.join(sorted(names)[:8]) or '(空)'))
        lines.append('')
        lines.append('布局常量（`core/memread/il2cpp.py`，**假定不变**）：'
                     'name@%#x / ns@%#x / typeDefinition@%#x / fields@%#x / '
                     'static_fields@%#x'
                     % (TYPEINFO_NAME_OFFSET, TYPEINFO_NS_OFFSET,
                        TYPEINFO_TYPEDEF_OFFSET, TYPEINFO_FIELDS_OFFSET,
                        TYPEINFO_STATIC_FIELDS_OFFSET))
    except Exception as e:                                   # noqa: BLE001
        ok = False
        lines.append('❌ 深检异常：%r' % e)
    finally:
        if proc is not None:
            try:
                proc.close()
            except Exception:                                # noqa: BLE001
                pass
    return ok, lines


def _expected_fields_for(class_name):
    """某个类「应该有哪些字段」—— 只用于抽查，不追求完整。"""
    return {
        'Engine': {'threadWork', 'gameMode', 'pvpFinished'},
        'DuelClient': {'duelHUD', 'engineInitializer'},
        'DuelLogController': {'m_LogBaseItemList', 'm_Host'},
        'DuelEndMessage': {'player0Name', 'messageText'},
        'ClientWork': {'s_data'},
        'PresentBoxViewController': {'pID', 'quantity'},
        'DeckEditViewController2': {'main', 'extra'},
    }.get(class_name, set())


# ---------------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(
        description='游戏版本更新前后自检（离线可跑，--live 需游戏在运行）')
    ap.add_argument('--snapshot', action='store_true',
                    help='存基线到 data/game_update_baseline.json（**更新前**跑）')
    ap.add_argument('--offline', action='store_true',
                    help='离线比对基线（不开游戏）')
    ap.add_argument('--live', action='store_true',
                    help='在线深检：定位 TypeInfo / 读字段表（要开游戏）')
    ap.add_argument('--game-dir', default=default_game_dir(),
                    help='游戏安装目录（默认 MD_GAME_DIR 或内置默认值）')
    ap.add_argument('--baseline', default=DEFAULT_BASELINE,
                    help='基线文件路径')
    args = ap.parse_args(argv)

    if not (args.snapshot or args.offline or args.live):
        ap.print_help()
        return 2

    print('游戏目录：%s' % args.game_dir)
    if not os.path.isdir(args.game_dir):
        print('❌ 目录不存在 —— 用 --game-dir 指定，或设环境变量 MD_GAME_DIR')
        return 2

    if args.snapshot:
        cur = snapshot(args.game_dir)
        os.makedirs(os.path.dirname(args.baseline), exist_ok=True)
        with open(args.baseline, 'w', encoding='utf-8', newline='') as fh:
            json.dump(cur, fh, ensure_ascii=False, indent=2)
        print('✅ 基线已写入 %s' % args.baseline)
        print('   Unity %s / metadata v%s / 依赖 %d 类 %d 字段'
              % (cur.get('unity_version'), (cur.get('metadata') or {}).get('version'),
                 len(cur.get('dep_classes') or []),
                 len(cur.get('dep_fields') or [])))
        print('   ⚠️ 这份文件是**本机数据**（游戏目录、版本因机器而异），'
              '已在 .gitignore 里 —— 别提交。')
        return 0

    if args.offline:
        if not os.path.exists(args.baseline):
            print('❌ 没有基线（%s）—— 更新前先跑 --snapshot' % args.baseline)
            return 2
        with open(args.baseline, encoding='utf-8') as fh:
            before = json.load(fh)
        after = snapshot(args.game_dir)
        rows = diff(before, after)
        bad = [r for r in rows if r[0] == 'BAD']
        warn = [r for r in rows if r[0] == 'WARN']
        print('\n基线：Unity %s / metadata v%s'
              % (before.get('unity_version'),
                 (before.get('metadata') or {}).get('version')))
        print('现在：Unity %s / metadata v%s'
              % (after.get('unity_version'),
                 (after.get('metadata') or {}).get('version')))
        print('')
        if not rows:
            print('✅ 没有发现差异 —— 更新看起来没动我们依赖的东西')
        for sev, text in rows:
            print('[%s] %s' % (sev, text))
        print('')
        if bad:
            print('🔴 有 %d 条 BAD —— 采集器大概已经断了，按 docs/游戏更新应对计划.md 分诊'
                  % len(bad))
            return 1
        if warn:
            print('🟡 有 %d 条 WARN —— 建议再跑一次 --live 确认' % len(warn))
            return 0
        print('下一步（要开游戏）：python tools/check_game_update.py --live')
        return 0

    # --live
    ok, lines = live_report(args.game_dir)
    print('')
    for line in lines:
        print(line)
    print('')
    if ok:
        print('✅ 在线深检通过 —— 采集链应该能用。仍建议实打一局验证落库。')
        return 0
    print('🔴 在线深检失败 —— 按 docs/游戏更新应对计划.md 的「症状 → 根因」表分诊')
    return 1


if __name__ == '__main__':
    sys.exit(main())
