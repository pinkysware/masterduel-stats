# -*- coding: utf-8 -*-
"""M7 界面层（PySide6）。**只有这一层会 import Qt** —— 见 ``docs/M7计划.md`` §三。

数据与逻辑一律在 ``core/``（``core/ui_state.py`` 读库出快照、``core/ui_actions.py``
负责所有写操作）。这一层不该出现 SQL、不该 import ``core.storage`` 以外的数据源。
"""
