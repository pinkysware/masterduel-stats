# -*- coding: utf-8 -*-
"""log_tailer —— 实时跟踪 Untapped 日志文件，产出新增行。

这是整个项目的地基模块（M1）。它要解决的核心问题：

1. **实时增量读取** —— 只读新增部分，不重复读历史
2. **文件轮转检测** —— log.log 满 1MB 后会变成 log.old.log，内容整体替换
3. **半行处理** —— 日志写到一半（没有换行符）时不能当完整行用，要等下一轮
4. **多文件切换** —— 用 log.old.log 补齐 log.log 之前的历史
5. **游标持久化** —— 记住读到哪了，重启不重复不丢

设计约束（务必遵守）：
  * **只读**。绝不写、不改、不移动 Untapped 的任何文件。
  * 不依赖文件 mtime（不可靠），用「大小变小 + 内容变化」判断轮转。

用法：
    tailer = LogTailer()                  # 默认路径
    for line in tailer.poll():            # 每次调用返回新增行
        print(line)

    # 首次运行，从现有日志末尾开始（不读历史）
    tailer = LogTailer(start_from='end')

    # 首次运行，读全部历史
    tailer = LogTailer(start_from='beginning')
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Iterator, Optional

def _default_log_dir() -> str:
    """定位 Untapped 数据目录。

    注意：**不能只依赖 %APPDATA%** —— 在 Git Bash 等环境下该变量为空。
    按可靠性依次尝试：APPDATA → USERPROFILE\\AppData\\Roaming → %APPDATA% + expanduser 兜底。
    """
    cands = []
    appdata = os.environ.get('APPDATA')
    if appdata:
        cands.append(appdata)
    up = os.environ.get('USERPROFILE')
    if up:
        cands.append(os.path.join(up, 'AppData', 'Roaming'))
    home = os.path.expanduser('~')
    if home and home != '~':
        cands.append(os.path.join(home, 'AppData', 'Roaming'))

    for base in cands:
        p = os.path.join(base, 'untapped-companion')
        if os.path.isdir(p):
            return p
    # 都不存在时，返回首选路径（让上层报出清晰错误）
    return os.path.join(cands[0] if cands else '', 'untapped-companion')


DEFAULT_LOG_DIR = _default_log_dir()
DEFAULT_MAIN = 'log.log'
DEFAULT_OLD = 'log.old.log'


class LogTailer:
    """日志增量读取器。

    参数
    ----
    log_dir : 日志目录，默认 %APPDATA%\\untapped-companion
    start_from : 'end' | 'beginning' | 'saved'
        * end       —— 首次运行从当前末尾开始（推荐：只要新的）
        * beginning —— 首次运行读全部历史（用于补数据）
        * saved     —— 只能用已保存的游标；没有游标就报错
    state_path : 游标持久化路径。None = 不持久化（纯内存）
    include_old : 是否也跟踪 log.old.log（补历史用）
    """

    def __init__(
        self,
        log_dir: str = DEFAULT_LOG_DIR,
        start_from: str = 'end',
        state_path: Optional[str] = None,
        include_old: bool = True,
    ):
        if start_from not in ('end', 'beginning', 'saved'):
            raise ValueError("start_from 必须是 'end' / 'beginning' / 'saved'")

        self.log_dir = log_dir
        self.start_from = start_from
        self.state_path = state_path
        self.include_old = include_old

        self.main_path = os.path.join(log_dir, DEFAULT_MAIN)
        self.old_path = os.path.join(log_dir, DEFAULT_OLD)

        # 每个文件的读取状态：{path: {'offset': int, 'size': int, 'inode': str}}
        self._files: dict[str, dict] = {}
        # 上次 poll 时各文件的大小/修改时间，用于检测轮转
        self._last_size: dict[str, int] = {}
        self._last_mtime: dict[str, float] = {}
        # 本次进程内是否真正从 old 读过内容（用于防重复继承）
        self._old_ever_read: bool = False
        #: ★ BUG-B2A-06：游标文件损坏时的说明（正常读完为 None）；工具可以打出来
        self.state_error: Optional[str] = None

        self._load_state()
        self._bootstrap()

    # ---------------------------------------------------------------- 状态存取
    def _load_state(self) -> None:
        if not self.state_path or not os.path.exists(self.state_path):
            return
        try:
            with open(self.state_path, encoding='utf-8') as f:
                saved = json.load(f)
            for path, st in (saved.get('files') or {}).items():
                self._files[path] = {
                    'offset': int(st.get('offset', 0)),
                    'size': int(st.get('size', 0)),
                    'inode': st.get('inode'),
                    'head_sig': st.get('head_sig'),
                }
        except Exception as e:                              # noqa: BLE001
            # ★ BUG-B2A-06：游标损坏不该让工具起不来，但**也不能静默** ——
            #   原来悄悄 `_files = {}`，于是 `start_from='beginning'`（历史回补）
            #   的语义被换成「按本次调用重新判 start_from」，用户以为补全了、
            #   实际只读到最近一点。现在：
            #   ① 把坏文件改名备份（`<path>.broken-<ts>`，不覆盖任何东西）；
            #   ② 记一行 `state_error`（调用方/工具可以打出来）。
            self.state_error = '游标损坏（%s）：%s' % (type(e).__name__, e)
            self._backup_bad_state()
            self._files = {}

    def _backup_bad_state(self) -> None:
        """把损坏的游标文件改名留档（`.broken-<时间戳>`）—— 绝不删。

        改名失败也不抛（文件名冲突 / 权限）：游标本就是可再生的缓存，
        但**留一份**便于事后查「为什么回补窗口变小了」。
        """
        if not self.state_path:
            return
        try:
            stamp = time.strftime('%Y%m%d_%H%M%S')
            os.replace(self.state_path, '%s.broken-%s' % (self.state_path, stamp))
        except OSError:
            pass

    def save_state(self) -> None:
        if not self.state_path:
            return
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.state_path)),
                        exist_ok=True)
            payload = {
                'saved_at': _now_str(),
                'files': {p: {'offset': s['offset'], 'size': s['size'],
                              'inode': s.get('inode'),
                              'head_sig': s.get('head_sig')}
                          for p, s in self._files.items()},
            }
            tmp = self.state_path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.state_path)      # 原子替换，避免写坏
        except Exception:
            pass

    # ---------------------------------------------------------------- 初始化
    def _file_key(self, path: str) -> Optional[str]:
        """文件的稳定标识。用 st_ino（Windows 上也有）+ 创建时间兜底。"""
        try:
            st = os.stat(path)
        except OSError:
            return None
        ino = getattr(st, 'st_ino', 0) or 0
        return '%s:%s' % (ino, int(getattr(st, 'st_ctime', 0) or 0))

    def _bootstrap(self) -> None:
        """首次建立**主日志**的起始偏移。

        注意：log.old.log 的生命周期由 `_read_old()` 自己管理（它需要根据
        「是否刚轮到 main」来决定继承还是真读），这里**不能**预先给它写游标，
        否则 `_read_old` 会把预置的 offset 当成「已经读过」而漏掉历史内容。
        """
        path = self.main_path
        if not os.path.exists(path):
            return
        size = os.path.getsize(path)
        key = self._file_key(path)

        known = self._files.get(path)
        if known and known.get('inode') == key:
            # 游标有效，且文件没被替换。
            #
            # ⚠️ 关键：`_last_size` 必须沿用**游标里记录的大小**，不能写成当前
            # 大小。否则文件在工具关闭期间「纯追加」了一段（很常见！）时，
            # `_detect_rotation` 会用「当前大小 == 刚写入的 _last_size」判定为
            # 「大小没变」，进而去比头部指纹 —— 而文件变长会让指纹不匹配，
            # 于是**误判成轮转**，整段历史被重读。
            self._last_size[path] = known.get('size', size)
            self._last_mtime[path] = self._safe_mtime(path)
            return

        if self.start_from == 'saved' and not known:
            raise RuntimeError(
                "start_from='saved' 但没有已保存的游标：%s" % path)

        # 决定起始位置
        if self.start_from == 'beginning':
            offset = 0
        elif self.start_from == 'end':
            offset = size
        else:  # saved，但游标对应的是另一个文件（被轮转替换了）
            offset = 0

        self._files[path] = {'offset': offset, 'size': size, 'inode': key,
                             'head_sig': self._head_sig(path)}
        self._last_size[path] = size
        self._last_mtime[path] = self._safe_mtime(path)

    # ---------------------------------------------------------------- 轮转检测
    def _safe_mtime(self, path: str):
        try:
            return os.stat(path).st_mtime
        except OSError:
            return None

    def _read_at(self, path: str, offset: int, size: int) -> str:
        with open(path, 'rb') as f:
            f.seek(offset)
            return f.read(size - offset).decode('utf-8', errors='replace')

    def _head_sig(self, path: str) -> Optional[str]:
        """读取文件开头的一小段做指纹，用于识别「同尺寸但被替换」。

        注意：**只读前 256 字节**，不加载整个文件。
        """
        try:
            with open(path, 'rb') as f:
                return hashlib.md5(f.read(256)).hexdigest()
        except OSError:
            return None

    def _detect_rotation(self, path: str, size: int) -> bool:
        """判断这个文件是否被整体替换（轮转），而不是单纯追加。

        依次尝试多个信号，任一命中即判定轮转：

          1. **inode 变化** —— 同名换了文件对象（最可靠，但 Windows 上
             取决于文件系统，实测 NTFS 覆盖写不换 inode，所以不能单靠它）
          2. **大小变小** —— 追加型日志绝不可能变小
          3. **头部指纹变化** —— 对付「大小恰好相同」的替换

        ⚠️ 关于第 3 条为什么不看 mtime：Windows 的 mtime 精度有限，
        同尺寸覆盖写很可能落在同一个时间刻度内，mtime 看起来没变。
        实测这会导致真实轮转被漏判。所以**只要大小没变，就直接比指纹**
        （只读前 256 字节，一次 poll 的开销可以忽略）。
        """
        prev = self._last_size.get(path)
        st = self._files.get(path)

        # 首次见到：不判轮转
        if prev is None or st is None:
            return False

        # 1. inode
        key = self._file_key(path)
        if key and st.get('inode') and key != st['inode']:
            return True

        # 2. 变小
        if size < prev:
            return True

        # 3. 大小没变 → 比头部指纹
        if size == prev:
            sig = self._head_sig(path)
            old_sig = st.get('head_sig')
            if sig and old_sig and sig != old_sig:
                return True

        return False

    # ---------------------------------------------------------------- 读取
    def _reconcile_startup_rotation(self) -> None:
        """处理「工具没在运行时发生了轮转」的情况。

        场景：上次运行结束时 log.log 已读到 offset=N，工具关闭期间 Untapped
        把它改名成 log.old.log，并新建了 log.log。重启后若把 log.old.log
        当成全新历史读一遍，对局记录就会翻倍。

        **它是 `_read_main` 轮转检测的兜底**，只在后者失效时才起作用：

          * inode 变化 / 大小变小 / 大小不变但指纹变了 → `_read_main` 能发现，
            轮转对账用不上。
          * ⚠️ **唯一漏网**：轮转后新 main 立刻被写入了比原来**更长**的内容。
            此时「大小变小」不成立、inode 若又被系统复用也不成立，
            `_read_main` 会误判为纯追加，只读尾巴 —— 而 old 的历史会被重读。

        判据（同时满足）：
          * old 的**头部指纹** == 游标里记录的 main 头部指纹
            （指纹一致 ⇒ 两者开头相同 ⇒ old 就是当初那个 main）
          * old 的大小 >= 游标里记录的 main 大小
        """
        if not self.include_old or not os.path.exists(self.old_path):
            return
        if not os.path.exists(self.main_path):
            return

        saved_main = self._files.get(self.main_path)
        if not saved_main:
            return
        saved_sig = saved_main.get('head_sig')
        saved_size = saved_main.get('size', 0)
        if not saved_sig or saved_size <= 0:
            return

        old_size = os.path.getsize(self.old_path)
        if old_size < saved_size:
            return

        old_sig = self._head_sig(self.old_path)
        if not old_sig or old_sig != saved_sig:
            return

        # 命中：old 就是当初已读完的 main → 继承游标
        known = self._files.get(self.old_path)
        if known is None:
            known = self._files[self.old_path] = {}
        known['offset'] = old_size
        known['size'] = old_size
        known['inode'] = self._file_key(self.old_path)
        known['head_sig'] = old_sig
        self._last_size[self.old_path] = old_size
        self._last_mtime[self.old_path] = self._safe_mtime(self.old_path)
        self._old_ever_read = True     # 明确标记：old 已消化，别再真读

        # ⚠️ 关键收尾：main 现在是**新文件**，游标 offset 来自旧文件，必须归零。
        # 否则会从旧偏移开始读，切出半行甚至整个错位。
        saved_main['offset'] = 0
        saved_main['inode'] = self._file_key(self.main_path)
        saved_main['head_sig'] = self._head_sig(self.main_path)
        saved_main['size'] = os.path.getsize(self.main_path)
        self._last_size[self.main_path] = saved_main['size']
        self._last_mtime[self.main_path] = self._safe_mtime(self.main_path)

    def _read_main(self) -> str:
        """读主日志（log.log）的新增内容。

        若检测到 log.log 被轮转替换，**先把旧内容读完再接受新文件** ——
        这样即使轮转发生在两次 poll 之间，也不会丢内容。
        """
        path = self.main_path
        if not os.path.exists(path):
            return ''
        size = os.path.getsize(path)
        # ★ 首次见到的文件按 `start_from` 给默认偏移（BUG-B2A-12）：
        #   原来固定 offset=0，于是 `_bootstrap()` 时文件还不存在、运行中
        #   才出现的场景下，`start_from='end'` 也会把**整份历史**当新内容吐出来。
        #    `beginning` / `saved`（游标丢了）仍从 0 读 —— 那正是「补历史」的语义。
        first_offset = size if self.start_from == 'end' else 0
        st = self._files.setdefault(
            path, {'offset': first_offset, 'size': size,
                   'inode': self._file_key(path)})
        if st.get('head_sig') is None:
            # 从磁盘游标恢复时 head_sig 为空，先补采样一次，
            # 否则首次同尺寸比对会失真。
            st['head_sig'] = self._head_sig(path)

        rotated = self._detect_rotation(path, size)

        self._last_size[path] = size
        self._last_mtime[path] = self._safe_mtime(path)

        if rotated:
            # 记录「轮转前读到哪」，供 old 继承判定
            self._rotated_from = {'path': path, 'offset': st.get('offset', 0),
                                  'size': st.get('size', 0)}
            st['offset'] = 0

        # inode / head_sig 每次都要刷新，作为下次「同尺寸替换」的比对基线
        st['inode'] = self._file_key(path)
        st['head_sig'] = self._head_sig(path)

        if st['offset'] > size:
            st['offset'] = 0
        st['size'] = size
        if st['offset'] >= size:
            return ''
        chunk = self._read_at(path, st['offset'], size)
        st['offset'] = size
        return chunk

    def _read_old(self) -> str:
        """读历史日志（log.old.log）。

        核心难点：log.old.log 的内容可能是**我们刚读过的 log.log**。
        若不做处理会重复统计对局。

        处理办法：
          * 若本次检测到 main 发生轮转，且 old 的大小 == main 轮转前的大小，
            则把 old 的游标直接设为它的末尾（内容已在读 main 时消化过）。
          * 否则按正常增量读。
        """
        path = self.old_path
        if not os.path.exists(path):
            return ''
        size = os.path.getsize(path)
        known = self._files.get(path)
        if known is None:
            # 首次见到 old：把当前内容视为「已存在但未读」，
            # 起始偏移按 bootstrap 的语义决定（beginning=0 / end=size）。
            if self.start_from == 'beginning':
                offset = 0
            else:
                offset = size
            known = self._files[path] = {
                'offset': offset, 'size': size,
                'inode': self._file_key(path), 'head_sig': self._head_sig(path),
            }
            self._last_size[path] = size
            self._last_mtime[path] = self._safe_mtime(path)
        elif known.get('head_sig') is None:
            # 从磁盘游标恢复时 head_sig 是空的，这里补采样一次
            known['head_sig'] = self._head_sig(path)
        st = known

        rot = getattr(self, '_rotated_from', None)
        ever_read = self._old_ever_read          # 本次进程内是否真正读过 old

        def _mark_consumed(reason):
            st['offset'] = size
            st['size'] = size
            st['inode'] = self._file_key(path)
            st['head_sig'] = self._head_sig(path)
            self._last_size[path] = size
            self._last_mtime[path] = self._safe_mtime(path)

        # ① 本次检测到 main 刚轮转，且 old 的大小 == main 轮转前的大小
        #    → old 就是「刚才那个 main」，内容已消化，直接标记为读完
        if rot and not ever_read and size > 0 and size == rot.get('size', 0):
            _mark_consumed('inherit-first')
            return ''

        # ② old 已被真正读过，又被整体替换（二次轮转）→ 同样是刚读过的 main
        if ever_read and self._detect_rotation(path, size):
            _mark_consumed('inherit-second')
            return ''

        self._last_size[path] = size
        self._last_mtime[path] = self._safe_mtime(path)
        st['head_sig'] = self._head_sig(path)

        if st['offset'] > size:
            st['offset'] = 0
        st['size'] = size
        if st['offset'] >= size:
            return ''
        chunk = self._read_at(path, st['offset'], size)
        st['offset'] = size
        self._old_ever_read = True
        return chunk

    def _emit(self, text: str, carry: str) -> tuple[list[str], str]:
        """把文本切成完整行，返回 (完整行列表, 残留的半行)。"""
        buf = carry + text
        # 统一用 \n 切；日志既有一致的 \r\n，也有多行堆栈的裸 \n
        parts = buf.split('\n')
        carry = parts.pop()                      # 最后一段可能不完整
        lines = [p.rstrip('\r') for p in parts]
        return lines, carry

    # ---------------------------------------------------------------- 对外接口
    def poll(self) -> list[str]:
        """返回自上次调用以来的新增**完整行**（已去掉换行符）。

        半行会留在内部缓冲，等后续内容补齐后再吐出。
        """
        out: list[str] = []
        carry = getattr(self, '_carry', '')

        # ⓪ 启动对账：处理「工具没运行时发生了轮转」（只做一次）
        if not getattr(self, '_reconciled', False):
            self._reconcile_startup_rotation()
            self._reconciled = True

        # ⚠️ 读取顺序很关键：
        #   必须先读 main，才能知道它是否刚被轮转；
        #   再读 old，才能判断 old 的内容是不是「刚读过的 main」。
        #   但**输出顺序**要保持时间序：old（更早）在前，main（更新）在后。
        self._rotated_from = None
        main_text = self._read_main() if os.path.exists(self.main_path) else ''
        old_text = ''
        if self.include_old and os.path.exists(self.old_path):
            old_text = self._read_old()

        for text in (old_text, main_text):
            if not text:
                continue
            lines, carry = self._emit(text, carry)
            out.extend(lines)

        self._carry = carry
        return out

    def poll_events(self) -> list[str]:
        """同 poll()，但只返回以 '[' 开头的有效日志行。

        JS 堆栈跟踪等续行会被丢掉 —— 它们对解析无用。
        """
        return [l for l in self.poll() if l.startswith('[')]

    # ---------------------------------------------------------------- 辅助
    def pending_partial(self) -> str:
        """当前缓冲里还没成行的残留内容（调试用）。"""
        return getattr(self, '_carry', '')

    def status(self) -> dict:
        """当前各文件的读取状态（调试/UI 用）。"""
        return {
            'log_dir': self.log_dir,
            'tracked': {
                os.path.basename(p): {'offset': s['offset'], 'size': s['size']}
                for p, s in self._files.items()
            },
            'carry_bytes': len(getattr(self, '_carry', '')),
        }


def _now_str() -> str:
    from datetime import datetime
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


# -------------------------------------------------------------------- CLI
if __name__ == '__main__':
    import sys
    import time
    import argparse

    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    ap = argparse.ArgumentParser(description='日志 tail（M1 自测）')
    ap.add_argument('--from', dest='start_from', default='end',
                    choices=['end', 'beginning', 'saved'])
    ap.add_argument('--state', default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'data', 'archive', 'tail_state.json'))
    ap.add_argument('--follow', action='store_true', help='持续跟踪')
    ap.add_argument('--interval', type=float, default=2.0)
    ap.add_argument('--grep', default=None, help='只显示含该关键词的行')
    ap.add_argument('--raw', action='store_true', help='显示所有行（含堆栈续行）')
    args = ap.parse_args()

    t = LogTailer(start_from=args.start_from, state_path=args.state)
    print('[M1] 状态: %s' % json.dumps(t.status(), ensure_ascii=False))

    def show(lines):
        for l in lines:
            if args.grep and args.grep not in l:
                continue
            print(l[:200])

    if not args.follow:
        show(t.poll() if args.raw else t.poll_events())
        t.save_state()
        sys.exit(0)

    print('[M1] 开始跟踪（Ctrl+C 退出）...')
    try:
        while True:
            show(t.poll() if args.raw else t.poll_events())
            t.save_state()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        t.save_state()
        print('\n[M1] 已退出，游标已保存')
