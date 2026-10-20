# -*- coding: utf-8 -*-
"""`tools/fetch_untapped_api.py` 单测 —— 多账号盖章（2026-10-03）。

口径（用户 10-03）：API 补的局都是「示例玩家」（fixture 假名）这个号的 —— 每局自带
`friendly_player_name`，入库顺手盖账号章（storage 侧只补空）。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from tools.fetch_untapped_api import api_to_row, import_games  # noqa: E402


def _game(ts=1759500000, mid='abc-1', name='示例玩家'):
    import time
    return {
        'match_id': mid,
        'match_start': ts or int(time.time()) - 3600,
        'game_mode': 3,
        'friendly_team_id': 1,
        'friendly_player_name': name,
        'opponent_player_name': '对手',
        'duels': [{'first_player_id': 1, 'turn_count': 3,
                   'finish_type': 1}],
        'result_type': 1,
        'initiative_player_id': 1,
    }


class ApiAccountStampTest(unittest.TestCase):
    """API 行自带账号名 + 补录入库时盖章（只补空）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='apiacct-')
        self.addCleanup(_rmtree, self.tmp)
        from core.storage import Storage
        self.st = Storage(os.path.join(self.tmp, 't.db'))
        self.st.init_schema()

    def test_api_to_row_carries_account(self):
        row = api_to_row(_game())
        self.assertEqual(row['account_name'], '示例玩家')

    def test_import_stamps_account_only_if_null(self):
        n_new, _have, _skip = import_games(self.st, [_game()])
        self.assertEqual(n_new, 1)
        got = self.st.get_match('api:abc-1')
        self.assertEqual(got['account_name'], '示例玩家')
        # 已有章的行重跑/回填不许冲掉（account 在 KEEP_IF_NEW_NULL）
        import io
        from tools import fetch_untapped_api as F
        row = F.api_to_row(_game(name='别人'))
        row['result'] = 'Lose'
        self.st.upsert_match(row)
        self.assertEqual(self.st.get_match('api:abc-1')['account_name'],
                         '示例玩家')


def _rmtree(path):
    import shutil
    shutil.rmtree(path, True)

class TestFetchAtomic(unittest.TestCase):
    """★ BUG-B9A-12：落盘原始 JSON 要原子。"""

    def test_bad_json_never_touches_file(self):
        import fetch_untapped_api as fa
        d = tempfile.mkdtemp(prefix='untapped_')
        self.addCleanup(__import__('shutil').rmtree, d, True)
        p = os.path.join(d, 'raw.json')
        with open(p, 'w', encoding='utf-8') as f:
            f.write('[1,2]')
        before = open(p, 'rb').read()
        old = fa.http_get
        fa.http_get = lambda url: '这不是 json'
        self.addCleanup(setattr, fa, 'http_get', old)
        #: 坏 JSON 在 **落盘前**就被 `parse_games` 拦下（JSONDecodeError / RuntimeError）
        with self.assertRaises(Exception):
            fa.fetch(p)
        self.assertEqual(open(p, 'rb').read(), before)




if __name__ == '__main__':
    unittest.main()
