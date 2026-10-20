# -*- coding: utf-8 -*-
"""M1 log_tailer 测试。

重点验证四类边界：
  1. 增量读取（只读新增）
  2. 文件轮转（log.log 变小 + 内容替换）
  3. 半行处理（写入中途不吐行）
  4. 游标持久化（重启不重不漏）

用临时目录模拟，不碰真实日志。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.log_tailer import LogTailer  # noqa: E402


def L(ts, mod, msg):
    """造一条日志行。"""
    return '[2026-09-10 %s] [info]  [%s] %s\r\n' % (ts, mod, msg)


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix='m1test_')
        self.main = os.path.join(self.d, 'log.log')
        self.old = os.path.join(self.d, 'log.old.log')
        self.state = os.path.join(self.d, 'state.json')

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def write(self, path, text, mode='a'):
        with open(path, mode, encoding='utf-8', newline='') as f:
            f.write(text)

    def mk(self, start_from='beginning', **kw):
        return LogTailer(log_dir=self.d, start_from=start_from,
                         state_path=self.state, **kw)


class TestIncremental(Base):
    def test_only_new_lines(self):
        """反复 poll 只应返回新增部分。"""
        self.write(self.main, L('10:00:00.000', 'A', 'one'))
        t = self.mk()
        r1 = t.poll()
        self.assertEqual(len(r1), 1)
        self.assertIn('one', r1[0])

        # 没有新内容 → 空
        self.assertEqual(t.poll(), [])

        # 追加两行
        self.write(self.main, L('10:00:01.000', 'A', 'two') + L('10:00:02.000', 'A', 'three'))
        r2 = t.poll()
        self.assertEqual(len(r2), 2)
        self.assertIn('two', r2[0])
        self.assertIn('three', r2[1])

    def test_start_from_end_skips_history(self):
        """start_from=end 应跳过已有内容。"""
        self.write(self.main, L('10:00:00.000', 'A', 'old1') + L('10:00:00.500', 'A', 'old2'))
        t = self.mk('end')
        self.assertEqual(t.poll(), [])
        self.write(self.main, L('10:00:01.000', 'A', 'new'))
        r = t.poll()
        self.assertEqual(len(r), 1)
        self.assertIn('new', r[0])


class TestPartialLine(Base):
    def test_half_line_not_emitted(self):
        """写到一半的行不能吐出来。"""
        t = self.mk()
        self.write(self.main, '[2026-09-10 10:00:00.000] [info]  [A] complete\r\n')
        self.write(self.main, '[2026-09-10 10:00:01.000] [info]  [A] incompl')
        r = t.poll()
        self.assertEqual(len(r), 1, '只应吐出完整的第1行')
        self.assertIn('complete', r[0])

        # 补全后半行
        self.write(self.main, 'ete\r\n')
        r2 = t.poll()
        self.assertEqual(len(r2), 1)
        self.assertIn('incomplete', r2[0])

    def test_carry_persists_across_polls(self):
        """半行要能跨多次 poll 保留。"""
        t = self.mk()
        self.write(self.main, '[2026-09-10 10:00:00.000] [info]  [A] abc')
        self.assertEqual(t.poll(), [])
        self.assertEqual(t.poll(), [])
        self.write(self.main, 'def\r\n')
        r = t.poll()
        self.assertEqual(len(r), 1)
        self.assertIn('abcdef', r[0])


class TestRotation(Base):
    def test_rotation_detected_by_size_shrink(self):
        """文件变小 → 判定轮转，从头读新内容。"""
        t = self.mk()
        self.write(self.main, L('10:00:00.000', 'A', 'gen1-1') * 1)
        t.poll()

        # 模拟轮转：log.log 内容被整体替换（变小）
        self.write(self.main, L('11:00:00.000', 'B', 'gen2-1'), mode='w')
        r = t.poll()
        self.assertTrue(any('gen2-1' in x for x in r),
                        '应读到轮转后的新内容: %r' % r)

    def test_no_duplicate_after_rotation(self):
        """轮转后不应重复吐旧内容。"""
        t = self.mk()
        big = ''.join(L('10:00:%02d.000' % i, 'A', 'line%d' % i) for i in range(20))
        self.write(self.main, big)
        first = t.poll()
        self.assertEqual(len(first), 20)

        # 轮转成更短的内容
        self.write(self.main, L('11:00:00.000', 'B', 'after-rotation'), mode='w')
        r = t.poll()
        self.assertEqual(len(r), 1, '只应有一条新内容，不能重复旧的: %r' % r)
        self.assertIn('after-rotation', r[0])

    def test_same_size_rotation(self):
        """★ 大小恰好相同、且写入间隔极短的替换也要能检测出来。

        这是实测会遇到的场景：Untapped 轮转后新文件恰好与旧文件等长，
        且两次 poll 间隔只有几百毫秒 —— Windows 的 mtime 精度可能覆盖不住，
        所以检测**不能依赖 mtime**，必须比对头部指纹。
        这里故意不加 sleep，模拟最恶劣的时序。
        """
        a = L('10:00:00.000', 'A', 'gen1-1')
        b = L('11:00:00.000', 'B', 'gen2-1')
        self.assertEqual(len(a), len(b), '测试前提：两行等长')

        self.write(self.main, a)
        t = self.mk()
        r1 = t.poll()
        self.assertEqual(len(r1), 1)

        self.write(self.main, b, mode='w')   # 不 sleep，直击 mtime 精度问题
        r2 = t.poll()
        self.assertTrue(any('gen2-1' in x for x in r2),
                        '等长替换应被识别为轮转: %r' % r2)
        # 不能重复吐旧内容
        self.assertFalse(any('gen1-1' in x for x in r2),
                         '不应重复旧内容: %r' % r2)

    def test_same_size_rotation_no_sleep_repeat(self):
        """同一场景连做 6 轮等长替换，确认不会时好时坏（防 flaky 回归）。"""
        t = self.mk()
        for i in range(6):
            line = L('10:%02d:00.000' % i, 'A', 'gen%d' % i)
            self.write(self.main, line, mode='w')
            r = t.poll()
            self.assertTrue(any('gen%d' % i in x for x in r),
                            '第 %d 轮漏读: %r' % (i, r))
            if i > 0:
                self.assertFalse(any('gen%d' % (i - 1) in x for x in r),
                                 '第 %d 轮重复上一条: %r' % (i, r))


class TestStartupRotation(Base):
    """★ 工具**没在运行时**发生轮转，重启后不能重复读历史。

    这是真实会遇到的场景：晚上打牌关掉工具，Untapped 在后台把 log.log
    轮转成 log.old.log，第二天打开工具 —— 若处理不当会把整段历史重读一遍，
    导致对局记录翻倍。

    两条防线（互为补充）：
      * inode 变化 → `_read_main` 判轮转 → `_read_old` 继承
      * inode 被系统复用（Windows 上会发生）→ 靠 `_reconcile_startup_rotation`
        用「大小相等 + 指纹一致」兜底
    """

    def _setup_rotated_state(self, body):
        """写入 body → 读完 → 存游标 → 轮转成 old + 清空 main。"""
        t = LogTailer(log_dir=self.d, start_from='beginning',
                      state_path=self.state)
        self.write(self.main, body)
        n = len(t.poll())
        t.save_state()
        shutil.copy2(self.main, self.old)
        open(self.main, 'wb').close()
        return n

    def test_rotation_while_down_not_reread(self):
        body = ''.join(L('10:%02d:00.000' % i, 'A', 'hist%d' % i)
                       for i in range(30))
        n = self._setup_rotated_state(body)
        self.assertEqual(n, 30)

        t2 = LogTailer(log_dir=self.d, start_from='beginning',
                       state_path=self.state)
        r2 = t2.poll()
        self.assertEqual(r2, [],
                         '重启后不应重复历史（实际吐出 %d 行）: %r'
                         % (len(r2), r2[:3]))
        t2.save_state()

        # 轮转后的新内容仍要能读到
        self.write(self.main, L('11:00:00.000', 'B', 'post-rotation'))
        r3 = t2.poll()
        self.assertEqual(len(r3), 1, '轮转后新行应正常读到: %r' % r3)
        self.assertIn('post-rotation', r3[0])

    def test_rotation_while_down_then_more_appends(self):
        """重启对账后继续追加，仍不重复、不丢。"""
        t = LogTailer(log_dir=self.d, start_from='beginning',
                      state_path=self.state)
        self.write(self.main, L('10:00:00.000', 'A', 'a1'))
        t.poll()
        t.save_state()

        # 关闭期间轮转，并在新 main 里写一条
        shutil.copy2(self.main, self.old)
        self.write(self.main, L('11:00:00.000', 'B', 'new1'), mode='w')

        t2 = LogTailer(log_dir=self.d, start_from='beginning',
                       state_path=self.state)
        r = t2.poll_events()
        self.assertEqual([x[-4:] for x in r], ['new1'],
                         '只应读到轮转后的新行: %r' % r)

    # ---------------- 直击 `_reconcile_startup_rotation` 的契约 ----------------
    def test_reconcile_same_size_rotation_while_down(self):
        """★ 关闭期间发生「等长轮转」+ inode 复用 → 只有对账能兜住。

        这是 `_reconcile_startup_rotation` 唯一不可替代的场景：
          * 新 main 与原 main **等长** → 「大小变小」信号失效
          * inode 被复用 → 「inode 变化」信号失效
          * 此时只能靠「old 的头部指纹 == 游标里 main 的头部指纹」判定。
        """
        import json
        old_line = L('10:00:00.000', 'A', 'AAAA-old-content')
        new_line = L('11:00:00.000', 'B', 'BBBB-new-content')
        self.assertEqual(len(old_line), len(new_line), '前提：等长')

        t = LogTailer(log_dir=self.d, start_from='beginning',
                      state_path=self.state)
        self.write(self.main, old_line)
        self.assertEqual(len(t.poll()), 1)
        t.save_state()

        # 关闭期间：轮转 + 新 main 写入等长内容 + inode 被复用
        shutil.copy2(self.main, self.old)
        self.write(self.main, new_line, mode='w')

        with open(self.state, encoding='utf-8') as f:
            s = json.load(f)
        st = os.stat(self.main)
        key = '%s:%s' % (getattr(st, 'st_ino', 0) or 0,
                         int(getattr(st, 'st_ctime', 0) or 0))
        for k in list(s['files']):
            if k.endswith('log.log'):
                s['files'][k]['inode'] = key
        with open(self.state, 'w', encoding='utf-8') as f:
            json.dump(s, f, ensure_ascii=False)

        t2 = LogTailer(log_dir=self.d, start_from='beginning',
                       state_path=self.state)
        r = t2.poll_events()
        joined = '\n'.join(r)
        self.assertNotIn('AAAA-old-content', joined,
                         '等长轮转 + inode 复用时历史被重复读出: %r' % r)
        self.assertIn('BBBB-new-content', joined,
                      '轮转后的新内容应读到: %r' % r)

    def test_reconcile_handles_inode_reuse(self):
        """inode 被复用时（配合大小变小信号）不重复读历史。

        注：这一条由「大小变小」和「对账」双重保障，去掉对账仍可能通过 ——
        它的价值在于防止将来有人拆掉大小信号时无声退化。
        """
        import json
        body = ''.join(L('10:%02d:00.000' % i, 'A', 'hist%d' % i)
                       for i in range(30))
        self._setup_rotated_state(body)
        self.write(self.main, L('11:00:00.000', 'B', 'fresh-after-rotate'),
                   mode='w')

        with open(self.state, encoding='utf-8') as f:
            s = json.load(f)
        st = os.stat(self.main)
        key = '%s:%s' % (getattr(st, 'st_ino', 0) or 0,
                         int(getattr(st, 'st_ctime', 0) or 0))
        for k in list(s['files']):
            if k.endswith('log.log'):
                s['files'][k]['inode'] = key
        with open(self.state, 'w', encoding='utf-8') as f:
            json.dump(s, f, ensure_ascii=False)

        t2 = LogTailer(log_dir=self.d, start_from='beginning',
                       state_path=self.state)
        r = t2.poll_events()
        joined = '\n'.join(r)
        self.assertNotIn('hist', joined, '历史被重复读出: %r' % r[:3])
        self.assertIn('fresh-after-rotate', joined,
                      '轮转后新内容应读到: %r' % r)

    def test_reconcile_does_not_fire_on_normal_append(self):
        """反例保护：正常追加（没轮转）时不要让对账误吞内容。

        用 start_from='saved' —— 这是生产用法：重启后接着游标读，
        只应拿到新增的那一行。
        """
        t = LogTailer(log_dir=self.d, start_from='saved',
                      state_path=self.state)
        self.write(self.main, L('10:00:00.000', 'A', 'one'))
        t.poll()
        t.save_state()
        self.write(self.main, L('10:00:01.000', 'A', 'two'))   # 纯追加

        t2 = LogTailer(log_dir=self.d, start_from='saved',
                       state_path=self.state)
        r = t2.poll_events()
        self.assertEqual([x[-3:] for x in r], ['two'],
                         '正常追加的 1 行必须读到: %r' % r)

    def test_reconcile_needs_size_match(self):
        """old 大小与保存的 main 大小不一致时，不能误判为轮转。"""
        t = LogTailer(log_dir=self.d, start_from='beginning',
                      state_path=self.state)
        self.write(self.main, L('10:00:00.000', 'A', 'orig'))
        t.poll()
        t.save_state()
        # old 里有别的、更长的内容（不是 main 的副本）
        self.write(self.old, L('09:00:00.000', 'X', 'other-old-content'))
        self.write(self.main, L('10:00:01.000', 'A', 'next'))  # 追加

        t2 = LogTailer(log_dir=self.d, start_from='beginning',
                       state_path=self.state)
        r = t2.poll_events()
        # old 的新内容要读到，main 的新增也要读到
        joined = '\n'.join(r)
        self.assertIn('other-old-content', joined, 'old 内容不应被吞: %r' % r)
        self.assertIn('next', joined, 'main 新增不应被吞: %r' % r)


class TestRealLog(Base):
    """对真实日志的冒烟测试（文件不存在则跳过）。"""

    def test_real_log_readable(self):
        from core.log_tailer import DEFAULT_LOG_DIR
        if not os.path.isdir(DEFAULT_LOG_DIR):
            self.skipTest('真实日志目录不存在')
        t = LogTailer(start_from='beginning')
        ev = t.poll_events()
        if not ev:
            self.skipTest('真实日志为空')
        # 全部应以 [ 开头
        self.assertTrue(all(x.startswith('[') for x in ev))
        # 应包含对局标记
        joined = '\n'.join(ev)
        self.assertIn('Ygom', joined)
        print('\n[真实日志] 读到 %d 条有效行' % len(ev))

    def test_dedup_against_manual_read(self):
        """tailer 读出的行数应与直接读文件一致（无丢无重）。"""
        from core.log_tailer import DEFAULT_LOG_DIR
        if not os.path.isdir(DEFAULT_LOG_DIR):
            self.skipTest('真实日志目录不存在')
        t = LogTailer(start_from='beginning')
        got = t.poll()
        # 手工按 \n 切一遍对比
        want = 0
        for name in ('log.old.log', 'log.log'):
            p = os.path.join(DEFAULT_LOG_DIR, name)
            if os.path.exists(p):
                with open(p, encoding='utf-8', errors='replace') as f:
                    want += len(f.read().split('\n')) - 1
        self.assertEqual(len(got), want,
                         'tailer 行数 %d != 手工 %d' % (len(got), want))


class TestState(Base):
    def test_state_persist_no_reread(self):
        """保存游标后重启，不应重复读。"""
        self.write(self.main, L('10:00:00.000', 'A', 'one') + L('10:00:01.000', 'A', 'two'))
        t1 = self.mk()
        r1 = t1.poll()
        self.assertEqual(len(r1), 2)
        t1.save_state()

        # 模拟重启：从已保存游标继续
        t2 = LogTailer(log_dir=self.d, start_from='saved', state_path=self.state)
        self.assertEqual(t2.poll(), [], '不应重复读已处理的内容')

        self.write(self.main, L('10:00:02.000', 'A', 'three'))
        r2 = t2.poll()
        self.assertEqual(len(r2), 1)
        self.assertIn('three', r2[0])

    def test_corrupt_state_recovers(self):
        """游标损坏时不应崩溃。"""
        with open(self.state, 'w', encoding='utf-8') as f:
            f.write('{ this is not json')
        self.write(self.main, L('10:00:00.000', 'A', 'x'))
        t = LogTailer(log_dir=self.d, start_from='end', state_path=self.state)
        self.assertIsInstance(t.poll(), list)


class TestMultiFile(Base):
    def test_reads_old_before_new(self):
        """log.old.log 应在 log.log 之前吐出（时间序）。"""
        self.write(self.old, L('09:00:00.000', 'OLD', 'from-old'))
        self.write(self.main, L('10:00:00.000', 'NEW', 'from-new'))
        t = self.mk()
        r = t.poll()
        self.assertEqual(len(r), 2)
        self.assertIn('from-old', r[0], 'old 应先出: %r' % r)
        self.assertIn('from-new', r[1], 'new 应后出: %r' % r)

    def test_missing_old_file_ok(self):
        """没有 log.old.log 时正常工作。"""
        self.write(self.main, L('10:00:00.000', 'A', 'only-main'))
        t = self.mk()
        r = t.poll()
        self.assertEqual(len(r), 1)

    def test_old_can_be_excluded(self):
        """include_old=False 时只读主日志。"""
        self.write(self.old, L('09:00:00.000', 'OLD', 'from-old'))
        self.write(self.main, L('10:00:00.000', 'NEW', 'from-new'))
        t = self.mk(include_old=False)
        r = t.poll()
        self.assertEqual(len(r), 1)
        self.assertIn('from-new', r[0])


class TestFiltering(Base):
    def test_poll_events_drops_stack_lines(self):
        """poll_events 应丢弃 JS 堆栈续行。"""
        text = (L('10:00:00.000', 'A', 'real-log-line')
                + '\n    at getValid (file:///C:/x.js:1:2)\n'
                + L('10:00:01.000', 'A', 'another-real'))
        self.write(self.main, text)
        t = self.mk()
        raw = t.poll()
        self.assertGreater(len(raw), 2, '原始行应包含堆栈')

        t2 = self.mk()
        ev = t2.poll_events()
        self.assertEqual(len(ev), 2, '有效行应只有 2 条: %r' % ev)
        self.assertTrue(all(x.startswith('[') for x in ev))

    def test_truncated_marker_handled(self):
        """[truncated] 这种被截断的标记要能正常读出。"""
        self.write(self.main,
                   '[2026-09-10 11:33:39.822] [verbose] [X] {"cards":[{"id":"10\n'
                   '[truncated]\r\n'
                   + L('11:33:39.823', 'A', 'after-trunc'))
        t = self.mk()
        r = t.poll()
        self.assertEqual(len(r), 3, '应得到 3 行（含 truncated）: %r' % r)
        self.assertIn('[truncated]', r[1])


class TestFileCreatedLater(Base):
    def test_log_appears_after_start(self):
        """启动时文件还不存在，后来才出现 → 应能读到。"""
        t = self.mk()
        self.assertEqual(t.poll(), [])
        self.write(self.main, L('10:00:00.000', 'A', 'created-later'))
        r = t.poll()
        self.assertEqual(len(r), 1)
        self.assertIn('created-later', r[0])

    def test_start_from_end_skips_history_when_file_appears_later(self):
        """★ BUG-B2A-12：`start_from='end'` 下「后出现的文件」不能吐全历史。

        旧行为：`_bootstrap()` 时文件不在 → 直接 return（不写游标）；
        之后 `_read_main()` 的 `setdefault(offset=0)` 把整份文件当新内容。
        """
        t = self.mk(start_from='end')
        self.assertEqual(t.poll(), [])
        # 文件出现时已经有历史 —— 不该被当成新内容
        self.write(self.main, L('09:00:00.000', 'A', 'ancient'))
        self.assertEqual(t.poll(), [], 'end 语义下不应读历史')
        # 后续追加的照常读
        self.write(self.main, L('10:00:00.000', 'A', 'fresh'))
        got = t.poll()
        self.assertEqual([x[-5:] for x in got], ['fresh'], got)

    def test_start_from_beginning_still_reads_history(self):
        """对照：`beginning` 下后出现的文件照旧读全部。"""
        t = self.mk(start_from='beginning')
        self.assertEqual(t.poll(), [])
        self.write(self.main, L('09:00:00.000', 'A', 'ancient'))
        got = t.poll()
        self.assertEqual([x[-7:] for x in got], ['ancient'], got)


if __name__ == '__main__':
    unittest.main(verbosity=2)
