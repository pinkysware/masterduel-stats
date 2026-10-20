# -*- coding: utf-8 -*-
"""`ui/first_run.py` 单测 —— 首跑播种（2026-10-10）。

盯三件事：
  1. **判据**：库空才播种；非空 / 已标记 / demo 都不重复起进程
  2. 🔴 **不传 `parent=app`** —— `MasterDuelUiApp` 是**普通类、不是 QObject**，
     传进去 `ScriptRunner` 会抛 `TypeError`，而那个异常被启动处的
     `try/except` 吞掉 → **首跑从未启动、卡组选择器搜不到任何卡组**，
     界面上毫无迹象（真实事故，见 `test_no_parent_arg`）
  3. 抓完要**丢 `DeckImages` 实例**（索引是懒建缓存，不丢界面还是旧图）
"""

import os
import shutil
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

from core.storage import Storage                            # noqa: E402
from ui import first_run as fr                              # noqa: E402


class _MemSettings(object):
    """**内存版 QSettings**。

    ⚠️ 单测环境没有 `QApplication` 时，`QSettings` 的 organizationName /
    applicationName **都是空串**，`setValue` 根本写不进去、`value()` 恒为
    None（实测踩过：设了值读回来还是 None，看起来像 `ensure_meta_decks`
    的标志逻辑坏了，其实是 QSettings 根本没初始化）。
    """

    store = {}

    def __init__(self, *a, **kw):
        #: 真 `QSettings('masterduel', 'M7')` 是带两个参数的（BUG-B7-09 起）；
        #: 替身只关心读写，参数收下即可。
        pass

    def value(self, key, default=None):
        return self.store.get(key, default)

    def setValue(self, key, val):
        self.store[key] = val

    def remove(self, key):
        self.store.pop(key, None)

    def sync(self):
        pass


class _FakeRunner(object):
    """替身 runner：记录是否被启动，不真起进程。"""

    instances = []

    def __init__(self, script, args=None, **kw):
        #: ⚠️ **模拟真实签名**：QObject 子类不接受非 QObject 的 parent。
        #: 有人传 `parent=<普通对象>` 时这里就该炸 —— 那正是线上事故。
        parent = kw.get('parent')
        if parent is not None and not hasattr(parent, 'deleteLater'):
            raise TypeError(
                "QObject.__init__() called with wrong argument types: %r"
                % type(parent).__name__)
        self.script, self.args = script, args
        self.started = False
        self.done = _Signal()
        _FakeRunner.instances.append(self)

    def start(self):
        self.started = True


class _Signal(object):
    def __init__(self):
        self.slots = []

    def connect(self, fn):
        self.slots.append(fn)

    def emit(self, *a):
        for fn in list(self.slots):
            fn(*a)


class _FakeApp(object):
    """**普通 Python 类**（不是 QObject）—— 复刻 MasterDuelUiApp 的类型特征。"""

    _demo = False

    def __init__(self, db_path):
        self.storage = Storage(db_path)
        self.storage.init_schema()
        self._imgs = object()
        self._firstrun_proc = None
        self.refreshed = 0

    def refresh(self):
        self.refreshed += 1


class FirstRunBase(unittest.TestCase):
    def setUp(self):
        _FakeRunner.instances = []
        _tmp = os.path.join(PROJ, 'tmp')
        os.makedirs(_tmp, exist_ok=True)
        self.tmp = tempfile.mkdtemp(prefix='frun_', dir=_tmp)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.db = os.path.join(self.tmp, 't.db')
        #: ⚠️ `ScriptRunner` 是在 `ensure_meta_decks` **函数内部** import 的，
        #:    不是模块属性 —— 所以只能打桩 `sys.modules['ui.script_runner']`。
        import sys as _sys
        import types as _types
        _real_mod = _sys.modules.get('ui.script_runner')
        _fake_mod = _types.ModuleType('ui.script_runner')
        _fake_mod.ScriptRunner = _FakeRunner
        _sys.modules['ui.script_runner'] = _fake_mod
        self.addCleanup(
            lambda: _sys.modules.__setitem__('ui.script_runner', _real_mod)
            if _real_mod else _sys.modules.pop('ui.script_runner', None))
        self._real_log = fr._log
        fr._log = lambda *a, **k: None
        self.addCleanup(setattr, fr, '_log', self._real_log)
        #: 打桩 `first_run` 模块里的 QSettings（**无 QApplication 时写不进去**，
        #: 见 `_MemSettings` 文档）。用测试专用键，别动用户真实设置。
        self._real_qs = fr.QSettings
        _MemSettings.store = {}
        fr.QSettings = _MemSettings
        self.addCleanup(setattr, fr, 'QSettings', self._real_qs)
        self._real_flag = fr.FLAG_KEY
        fr.FLAG_KEY = 'firstrun/test_flag_tmp'
        self.addCleanup(setattr, fr, 'FLAG_KEY', self._real_flag)

    def tearDown(self):
        pass

    def _seed_decks(self, app, n=3):
        st = app.storage
        st.replace_meta_decks([
            dict(name='Deck%d' % i, kind='deck_type', name_cn='卡组%d' % i,
                 source='masterduelmeta') for i in range(n)], prune=False)


class TestJudgement(FirstRunBase):
    def test_empty_db_seeds(self):
        """库空 → 起 runner 并 start。"""
        app = _FakeApp(self.db)
        self.assertTrue(fr.meta_decks_empty(app.storage))
        r = fr.ensure_meta_decks(app)
        self.assertIsNotNone(r)
        self.assertTrue(r.started)

    def test_non_empty_db_does_not_seed(self):
        """库非空 → **不再联网**（只记标志）。"""
        app = _FakeApp(self.db)
        self._seed_decks(app)
        self.assertFalse(fr.meta_decks_empty(app.storage))
        self.assertIsNone(fr.ensure_meta_decks(app))
        self.assertEqual(_FakeRunner.instances, [], '库非空时不该起进程')

    def test_demo_mode_skipped(self):
        """demo 模式 → 不联网、不写真库。"""
        app = _FakeApp(self.db)
        app._demo = True
        self.assertIsNone(fr.ensure_meta_decks(app))
        self.assertEqual(_FakeRunner.instances, [])

    def test_flag_prevents_second_run(self):
        """已标记过 → 不重复起进程。"""
        _MemSettings.store[fr.FLAG_KEY] = 1
        app = _FakeApp(self.db)
        self.assertIsNone(fr.ensure_meta_decks(app))
        self.assertEqual(_FakeRunner.instances, [])

    def test_already_running_guard(self):
        """已在抓 → 不重复起。"""
        app = _FakeApp(self.db)
        app._firstrun_proc = object()
        self.assertIsNone(fr.ensure_meta_decks(app))


class TestRealBugNoParentArg(FirstRunBase):
    """🔴 回归锁：`MasterDuelUiApp` 不是 QObject，传 parent 必炸。"""

    def test_no_parent_arg(self):
        """`MasterDuelUiApp` 是**普通类** —— 传 `parent=` 会被 QObject 拒绝。

        真实事故（2026-10-10 用户实测发布包）：首跑播种抛的 TypeError
        被 `ui/app.py` 启动处的 `try/except` 吞掉，**首跑从未启动**：
        `first_run.log` 只留「开始」、没有「完成」，`meta_decks` 永远 0 行，
        表现为**卡组选择器搜不到任何卡组**，界面上毫无错误迹象。
        `_FakeRunner` 在拿到非 QObject 的 parent 时会抛同样的 TypeError
        —— 所以这条测试能抓住回归。
        """
        app = _FakeApp(self.db)
        self.assertFalse(hasattr(app, 'deleteLater'),
                         '这个假对象必须不是 QObject（复刻真 app）')
        # 真代码在这里必须**不抛**
        r = fr.ensure_meta_decks(app)
        self.assertIsNotNone(r, 'ensure_meta_decks 抛异常 = 首跑播种根本没启动')
        self.assertTrue(r.started)


class TestOnDone(FirstRunBase):
    def test_done_drops_deck_images_cache(self):
        """🔴 抓完必须 `app._imgs = None`。

        `DeckImages` 的别名索引是**懒建一次**的缓存（`_ensure_index`），
        抓图前建的实例永远看不到新落库的 `img_file` ——
        不丢就是「抓成功了但界面还是默认图」。
        """
        app = _FakeApp(self.db)
        runner = fr.ensure_meta_decks(app)
        old = app._imgs
        runner.done.emit(0)
        self.assertIsNone(app._imgs, '必须丢掉缓存的 DeckImages 实例')
        self.assertIsNone(app._firstrun_proc, '抓完要清引用')
        self.assertIsNotNone(old)

    def test_done_refreshes_ui(self):
        """抓完要刷新界面（否则用户还得手动切页）。"""
        app = _FakeApp(self.db)
        runner = fr.ensure_meta_decks(app)
        runner.done.emit(0)
        self.assertEqual(app.refreshed, 1)

    def test_failure_still_clears_cache(self):
        """失败也要丢缓存 + 刷新（下次启动会重试）。"""
        app = _FakeApp(self.db)
        runner = fr.ensure_meta_decks(app)
        runner.done.emit(2)
        self.assertIsNone(app._imgs)
        self.assertEqual(app.refreshed, 1)


if __name__ == '__main__':
    unittest.main()