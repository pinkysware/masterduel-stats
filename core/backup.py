# -*- coding: utf-8 -*-
"""全量备份 / 恢复（2026-10-24 用户需求：「卸载保留数据后重装，保留的数据不完全，
导出应该能导出整个软件数据」）。

## 为什么需要它
NSIS 卸载器保留 ``$INSTDIR\data``，但仍然会丢：

* 程序目录里按「素材」管理的**用户可改文件**（卡组覆盖表
  ``deck_name_cn.csv`` / 自定义卡组图 ``deck_images_custom/`` /
  自定义参照库 ``mdm_deck_cards_custom.json``）不在卸载保护范围；
* 换目录 / 装到 Program Files 重装时数据位置会变（``data/`` vs
  ``%LOCALAPPDATA%``），旧数据躺在旧目录，新装的程序看不到；
* 跨机器迁移（2026-10-24 用户把整台测试机数据拷来分析）想要
  「一个文件带走全部」。

## 口径
* **备份**（:func:`make_backup`）= 一个 zip，三块内容：
  - ``data/...`` —— DATA_DIR **全部**用户数据（库 / 对局记录 / 状态 /
    缓存，几 MB 级，全带上省得恢复后缺图）；
  - ``appdata/...`` —— 程序目录（ASSET_DIR）里用户可改的三样；
  - 顶层 ``backup.json``（时间 / 版本戳）+ ``paths.ini``（数据目录偏好，
    存在才带）。
* **恢复分两步**（不能热做 —— 界面 / 采集器开着时 SQLite 被锁，
  Windows 覆盖打开中的 db 直接 PermissionError）：
  1. :func:`stage_restore`：解到 ``DATA_DIR/_restore_staging/`` + 写
     ``restore_pending.json``。只写新文件，**不碰现有数据**（后悔了把
     staging 一删即可）；
  2. :func:`apply_pending`：**下次启动最早期**（任何 db 打开之前）调用 ——
     staging 应用到真实位置（合并覆盖），清 staging 与标志。
     失败（比如采集器还在）就**留着 pending 下次再试**，绝不半途而废。
* 恢复是**合并覆盖、不删现有多余文件**（保守口径：宁可多留也不错删）。
* staging / pending 标志自身**绝不进备份**（防套娃）。
"""
from __future__ import annotations

import json
import os
import shutil
import time
import zipfile
from typing import List, Optional, Tuple

from core import paths

#: 程序目录（ASSET_DIR）里**用户会改**的三样 —— 卸载重装最容易丢的就是它们
APPDATA_ITEMS: Tuple[str, ...] = ('deck_name_cn.csv', 'deck_images_custom',
                                  'mdm_deck_cards_custom.json')

#: staging 目录 / pending 标志（都在 DATA_DIR 下；绝不进备份）
STAGING_NAME = '_restore_staging'
PENDING_NAME = 'restore_pending.json'

#: 备份时排除（相对 DATA_DIR 的名字；pid 重装后无意义，staging/pending 防套娃）
EXCLUDE_NAMES = frozenset((STAGING_NAME, PENDING_NAME, 'duel_collector.pid'))


def _walk_files(base: str) -> List[str]:
    """base 下所有文件的**相对路径**（/ 分隔；不存在返回空）。"""
    out: List[str] = []
    if not os.path.isdir(base):
        return out
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in sorted(dirs) if d not in EXCLUDE_NAMES]
        for f in sorted(files):
            if f in EXCLUDE_NAMES:
                continue
            rel = os.path.relpath(os.path.join(root, f), base)
            out.append(rel.replace(os.sep, '/'))
    return out


def backup_items() -> List[Tuple[str, str]]:
    """要备份的 ``(磁盘绝对路径, zip 内相对路径)`` 清单 —— 纯函数，单测锁。"""
    out: List[Tuple[str, str]] = []
    for rel in _walk_files(paths.DATA_DIR):
        out.append((os.path.join(paths.DATA_DIR, *rel.split('/')),
                    'data/' + rel))
    for name in APPDATA_ITEMS:
        p = os.path.join(paths.ASSET_DIR, name)
        if os.path.isfile(p):
            out.append((p, 'appdata/' + name))
        elif os.path.isdir(p):
            for rel in _walk_files(p):
                out.append((os.path.join(p, *rel.split('/')),
                            'appdata/%s/%s' % (name, rel)))
    pref = paths._config_path()
    if os.path.isfile(pref):
        out.append((pref, 'paths.ini'))
    return out


def make_backup(dest: str, note: str = '') -> dict:
    """把全部用户数据打进一个 zip → 报告（文件数 / 字节数）。

    ``dest`` 由调用方（文件对话框）保证可写；这里失败抛 OSError 让 UI 提示。
    """
    items = backup_items()
    stamp = {'format': 1,
             'created_at': time.strftime('%Y-%m-%d %H:%M:%S'),
             'app_version': _app_version(),
             'data_dir': paths.DATA_DIR,
             'note': note}
    parent = os.path.dirname(os.path.abspath(dest))
    if parent:
        os.makedirs(parent, exist_ok=True)
    n_bytes = 0
    with zipfile.ZipFile(dest, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('backup.json', json.dumps(stamp, ensure_ascii=False,
                                             indent=2))
        for src, rel in items:
            z.write(src, rel)
            n_bytes += os.path.getsize(src)
    return {'files': len(items), 'bytes': n_bytes, 'dest': dest,
            'stamped_at': stamp['created_at']}


def read_stamp(zf: zipfile.ZipFile) -> dict:
    """读 zip 里的 backup.json（没有就空 dict —— 单测 / 展示用）。"""
    try:
        return json.loads(zf.read('backup.json').decode('utf-8'))
    except (KeyError, ValueError):
        return {}


def staging_path() -> str:
    return os.path.join(paths.DATA_DIR, STAGING_NAME)


def pending_path() -> str:
    return os.path.join(paths.DATA_DIR, PENDING_NAME)


def pending_exists() -> bool:
    return os.path.isfile(pending_path())


def stage_restore(zip_path: str) -> dict:
    """恢复第一步：解到 staging + 写 pending 标志（**不碰任何现有数据**）。

    采集器在跑也没关系（这步只写 staging）；真正应用在下次启动
    :func:`apply_pending` —— 但采集器活着时它也应用不了，所以 UI 在调这步
    **之前**应先拦「采集器正在运行」（心跳新鲜就先让用户停掉再恢复）。
    """
    staging = staging_path()
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist()
                 if not n.endswith('/') and n != 'backup.json']
        z.extractall(staging, members=names)
    pending = {'format': 1, 'staged_at': time.strftime('%Y-%m-%d %H:%M:%S'),
               'source_zip': os.path.abspath(zip_path), 'files': len(names)}
    os.makedirs(os.path.dirname(pending_path()), exist_ok=True)
    with open(pending_path(), 'w', encoding='utf-8', newline='') as f:
        json.dump(pending, f, ensure_ascii=False, indent=2)
    return pending


def _merge_tree(src: str, dst: str) -> int:
    """src 覆盖合并进 dst（同名覆盖、异名保留）→ 应用了的文件数。"""
    n = 0
    for rel in _walk_files(src):
        s = os.path.join(src, *rel.split('/'))
        d = os.path.join(dst, *rel.split('/'))
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.copy2(s, d)
        n += 1
    return n


def apply_pending() -> Optional[dict]:
    """恢复第二步：**启动最早期**调用 —— staging 应用到真实位置，清场。

    * 没有 pending → 返回 None（普通启动零开销）。
    * 应用失败（典型：采集器还锁着 db → PermissionError）→ **pending 保留**，
      返回 ``{'ok': False, 'error': ...}``，下次启动再试。
    * 成功 → 清 staging + pending，返回报告。
    """
    if not pending_exists():
        return None
    staging = staging_path()
    if not os.path.isdir(staging):
        try:
            os.remove(pending_path())
        except OSError:
            pass
        return None
    applied = {'ok': True, 'data_files': 0, 'app_files': 0,
               'at': time.strftime('%Y-%m-%d %H:%M:%S')}
    try:
        d_src = os.path.join(staging, 'data')
        if os.path.isdir(d_src):
            applied['data_files'] = _merge_tree(d_src, paths.DATA_DIR)
        a_src = os.path.join(staging, 'appdata')
        if os.path.isdir(a_src):
            applied['app_files'] = _merge_tree(a_src, paths.ASSET_DIR)
        pref = os.path.join(staging, 'paths.ini')
        if os.path.isfile(pref):
            shutil.copy2(pref, paths._config_path())
            applied['paths_ini'] = True
    except OSError as e:
        applied['ok'] = False
        applied['error'] = str(e)
        return applied                       # 留 pending，下次再试
    shutil.rmtree(staging, ignore_errors=True)
    try:
        os.remove(pending_path())
    except OSError:
        pass
    return applied


def _app_version() -> str:
    """读 core/version.py 的 __version__（不 import —— 备份路径不依赖包状态）。"""
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     'core', 'version.py')
    try:
        with open(p, encoding='utf-8') as f:
            for line in f:
                if line.startswith('__version__'):
                    return line.split('=', 1)[1].strip().strip(chr(39) + chr(34))
    except OSError:
        pass
    return ''
