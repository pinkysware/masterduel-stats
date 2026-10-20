# -*- coding: utf-8 -*-
"""`tools/build_card_names.py` 的上游检测 / 一键更新（M4）单测。

联网一律**打桩**（`fetcher` 注入口），不打真网：
真网实跑的记录（md5 一致、`--update` 跳过下载、A/B 分类命中 22747）见接力棒。

重点盯三件容易错的事：
  1. **比对键是 `cid`**（游戏内卡 id），不是 `id`（Konami 密码）—— 比错满屏假差异；
  2. **A / B 两类必须分开**（B 类重跑 build 一百次也没用，别让人白忙）；
  3. **失败不写文件**（半份快照比没有更糟）。
"""

import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, 'tools'))

import build_card_names as bcn                        # noqa: E402
from core import card_names as cn                     # noqa: E402


def pack(cards):
    """按上游形态打一个 zip（里面只有一个 cards.json）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('cards.json', json.dumps(cards, ensure_ascii=False))
    return buf.getvalue()


def pack_md5(cards):
    """上游公布的那个 MD5 = **包内 cards.json** 的 md5（不是 zip 自己的！）。"""
    return hashlib.md5(json.dumps(cards, ensure_ascii=False).encode('utf-8')).hexdigest()


def read_bytes(path):
    """读文件字节（用 with —— 别在测试里留 ResourceWarning）。"""
    with open(path, 'rb') as f:
        return f.read()


class FakeNet(object):
    """假网络：按 URL 返回预设内容，并**记账**（用来断言到底下没下载）。"""

    def __init__(self, md5, cards=None, fail=None):
        self.md5 = md5
        self.cards = cards
        self.fail = fail
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        if self.fail is not None:
            raise self.fail
        if url == bcn.URL_MD5:
            return ('"%s"' % self.md5).encode('utf-8')
        if url == bcn.URL_ZIP:
            return pack(self.cards or {})
        raise AssertionError('不该请求这个 URL：%s' % url)


class Base(unittest.TestCase):

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix='bcn_')
        self.ygo = os.path.join(self.d, 'cards.json')
        self.out = os.path.join(self.d, 'card_names.json')
        cn.clear_cache()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)
        cn.clear_cache()

    def write_snapshot(self, cards):
        with open(self.ygo, 'w', encoding='utf-8', newline='') as f:
            json.dump(cards, f, ensure_ascii=False)
        return hashlib.md5(read_bytes(self.ygo)).hexdigest()

    def read_snapshot(self):
        with open(self.ygo, encoding='utf-8') as f:
            return json.load(f)


class TestPure(Base):
    """纯函数：cid 比对 / A-B 分类。"""

    def test_read_upstream_cids_keys_on_cid(self):
        """★ 用 cid 比对 —— 拿 id（Konami 密码）比会得到一堆假差异。"""
        raw = {'a': {'cid': 20500, 'id': 42141493},
               'b': {'cid': 4007, 'id': 89631139},
               'c': {'id': 12345678},          # 只有密码：跳过，不瞎猜
               'd': 'garbage',
               'e': {'cid': '20500'}}          # 字符串 cid 也认
        self.assertEqual(bcn.read_upstream_cids(raw), {20500, 4007})
        # list 形态也认（本地快照是 dict，但别假设）
        self.assertEqual(bcn.read_upstream_cids([{'cid': '4007'}]), {4007})
        self.assertEqual(bcn.read_upstream_cids({}), set())

    def test_classify_unknown_ab(self):
        """真实锚点：22747（S:P 标准 id）/ 3002 / 3114 只在游戏卡表 -> B 类。"""
        cls = bcn.classify_unknown([22747, 3002, 3114, 20500, 20500],
                                   upstream_cids={20500, 4007})
        self.assertEqual(cls['A'], [20500])
        self.assertEqual(cls['B'], [3002, 3114, 22747])
        self.assertEqual(bcn.classify_unknown([], set()), {'A': [], 'B': []})

    def test_as_ints(self):
        self.assertEqual(bcn.as_ints('22747, 3002'), [3002, 22747])
        self.assertEqual(bcn.as_ints('22747，3002'), [3002, 22747])
        self.assertEqual(bcn.as_ints(['22747', 'x', None]), [22747])
        self.assertEqual(bcn.as_ints(''), [])


class TestDump(Base):
    """`dump()` 的 generated_at（P3-4：往返 + 老文件兼容）。"""

    def test_generated_at_roundtrip_and_legacy(self):
        bcn.dump({20500: ['多多迷宝', 'Mulcharmy', 'マルチャミー']}, self.out,
                 generated_at='2026-09-13T10:00:00')
        with open(self.out, encoding='utf-8') as f:
            raw = json.load(f)
        self.assertEqual(raw['generated_at'], '2026-09-13T10:00:00')
        cn.clear_cache()
        self.assertEqual(cn.display(20500, 'jp', cn.load(self.out)),
                         'マルチャミー')
        # 不传 -> 用当前时间（ISO 形状）
        bcn.dump({1: ['x', '', '']}, self.out)
        with open(self.out, encoding='utf-8') as f:
            self.assertRegex(json.load(f)['generated_at'],
                             r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$')
        # 老文件（没这个键）照样读得出来
        with open(self.out, 'w', encoding='utf-8', newline='') as f:
            json.dump({'v': 1, 'names': {'20500': ['旧名', '', '']}}, f,
                      ensure_ascii=False)
        cn.clear_cache()
        self.assertEqual(cn.display(20500, 'cn', cn.load(self.out)), '旧名')


class TestCheckUpdate(Base):

    def test_fresh_skips_download(self):
        """本地 md5 == 上游 -> 不下载 13.7MB，直接用本地快照分类。"""
        md5 = self.write_snapshot({'a': {'cid': 20500}})
        net = FakeNet(md5)
        rep = bcn.check_update([22747, 20500],
                               records_dir=os.path.join(self.d, 'none'),
                               ygocdb_path=self.ygo, fetcher=net)
        self.assertTrue(rep['fresh'])
        self.assertEqual(net.calls, [bcn.URL_MD5], '不该下载 zip')
        self.assertEqual(rep['upstream_cards'], 1)
        self.assertEqual(rep['A'], [20500])
        self.assertEqual(rep['B'], [22747])
        self.assertFalse(os.path.exists(self.out), '只读，不许写文件')

    def test_stale_downloads_and_verifies_md5(self):
        self.write_snapshot({'a': {'cid': 1}})            # 本地旧
        cards = {'a': {'cid': 20500}, 'b': {'cid': 4007}}
        net = FakeNet(pack_md5(cards), cards=cards)
        rep = bcn.check_update([], records_dir=os.path.join(self.d, 'none'),
                               ygocdb_path=self.ygo, fetcher=net)
        self.assertFalse(rep['fresh'])
        self.assertEqual(net.calls, [bcn.URL_MD5, bcn.URL_ZIP])
        self.assertEqual(rep['upstream_cards'], 2)
        self.assertFalse(os.path.exists(self.out))

    def test_md5_mismatch_raises(self):
        """公布的 md5 与下载内容不符（半截包）-> 报错。"""
        self.write_snapshot({'a': {'cid': 1}})
        net = FakeNet('0' * 32, cards={'a': {'cid': 1}})
        with self.assertRaises(RuntimeError) as cm:
            bcn.check_update([], records_dir=os.path.join(self.d, 'none'),
                             ygocdb_path=self.ygo, fetcher=net)
        self.assertIn('md5', str(cm.exception))


class TestUpdate(Base):

    def test_refresh_snapshot_and_rebuild(self):
        self.write_snapshot({'x': {'cid': 1, 'en_name': 'old'}})
        cards = {'a': {'cid': 90001, 'en_name': 'New One'},
                 'b': {'cid': 90002, 'en_name': 'New Two'}}
        net = FakeNet(pack_md5(cards), cards=cards)
        # 游戏卡表不在本测试范围（真表 14298 张，读它只会拖慢单测）
        with mock.patch.object(bcn, 'read_card_data', lambda: {}):
            rep = bcn.update(ygocdb_path=self.ygo, out=self.out, fetcher=net)
        self.assertTrue(rep['refreshed'])
        self.assertEqual(set(self.read_snapshot()), {'a', 'b'})
        with open(self.out, encoding='utf-8') as f:
            names = json.load(f)['names']
        self.assertEqual(names['90001'], ['', 'New One', ''])
        self.assertEqual([n for n in os.listdir(self.d) if n.endswith('.tmp')], [],
                         '不许留 .tmp 残渣')

    def test_already_fresh_no_download_but_still_rebuilds(self):
        md5 = self.write_snapshot({'a': {'cid': 90001, 'en_name': 'Same'}})
        net = FakeNet(md5)
        with mock.patch.object(bcn, 'read_card_data', lambda: {}):
            rep = bcn.update(ygocdb_path=self.ygo, out=self.out, fetcher=net)
        self.assertFalse(rep['refreshed'])
        self.assertEqual(net.calls, [bcn.URL_MD5])
        self.assertTrue(os.path.exists(self.out), '仍要重跑 build')

    def test_failure_writes_nothing(self):
        """网络失败 -> 异常 + **一个字都不写**（半份快照比没有更糟）。"""
        self.write_snapshot({'a': {'cid': 1}})
        before = read_bytes(self.ygo)
        net = FakeNet('x', fail=OSError('timeout'))
        with mock.patch.object(bcn, '_SLEEP', lambda s: None):   # 别真睡 6 秒
            with self.assertRaises(RuntimeError) as cm:
                bcn.update(ygocdb_path=self.ygo, out=self.out, fetcher=net)
        self.assertIn('timeout', str(cm.exception))
        self.assertEqual(net.calls, [bcn.URL_MD5] * 3, '要重试 3 次才放弃')
        self.assertEqual(read_bytes(self.ygo), before)
        self.assertFalse(os.path.exists(self.out))
        # 下载阶段失败同理（md5 已拿到，但包坏了）
        net2 = FakeNet('0' * 32, cards={'a': {'cid': 1}})
        with self.assertRaises(RuntimeError):
            bcn.update(ygocdb_path=self.ygo, out=self.out, fetcher=net2)
        self.assertEqual(read_bytes(self.ygo), before)
        self.assertFalse(os.path.exists(self.out))


class TestCollectUnknown(Base):

    def test_collect_from_records(self):
        for name, ids in (('record_20260913_120045', [3002, 22747]),
                          ('record_20260913_130045', [22747])):
            rec = os.path.join(self.d, name)
            os.makedirs(rec)
            with open(os.path.join(rec, 'duel.json'), 'w',
                      encoding='utf-8', newline='') as f:
                json.dump({'summary': {'unknown_card_ids': ids}}, f)
        os.makedirs(os.path.join(self.d, 'record_20260913_140045'))   # 没 duel.json
        bad = os.path.join(self.d, 'record_20260913_150045')
        os.makedirs(bad)
        with open(os.path.join(bad, 'duel.json'), 'w',
                  encoding='utf-8', newline='') as f:
            f.write('{不是 json')                                       # 坏文件不炸
        self.assertEqual(bcn.collect_unknown_from_records(self.d), [3002, 22747])
        self.assertEqual(bcn.collect_unknown_from_records(
            os.path.join(self.d, 'nope')), [])

class TestDumpAtomic(unittest.TestCase):
    """★ BUG-B9A-11：生产卡名表走原子写。"""

    def test_failure_keeps_old_file(self):
        d = tempfile.mkdtemp(prefix='cardnames_')
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, 'card_names.json')
        bcn.dump({1: ['x', 'y', 'z']}, p)
        before = open(p, 'rb').read()
        # 目标路径是个**目录** → `os.replace` 失败（目录路径不会被自动建）
        blocked = os.path.join(d, 'blocked')
        os.makedirs(blocked)
        with self.assertRaises((SystemExit, OSError)):
            bcn.dump({2: ['a', 'b', 'c']}, blocked)
        self.assertEqual(open(p, 'rb').read(), before)




if __name__ == '__main__':
    unittest.main()
