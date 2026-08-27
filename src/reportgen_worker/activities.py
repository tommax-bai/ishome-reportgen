"""Temporal activities：所有 IO 与重计算收口在此。

注册名唯一真源：ishome-contracts `activities/registry.md`，**只增不改**——改注册名
会破坏历史 workflow 重放，等同于改线上协议；新增走 contracts 仓 PR 评审。
命名规则（规范 §2.4）：注册名 = kebab-case 显式声明；函数名 = 同词 snake_case
动词前置。

成文线首批 activity **待注册**（图 v0.2 §3 单元子图：单元成文/页面装配/册级校验）。
纪律预置（实现时逐条兑现）：一台引擎挂 N 个域资产包，**禁止按域拆 activity**
（规则 5.0c，dom- 作参数不作注册名）；出口过检不合格重写 ≤2 轮后 failed verdict
上抛，绝不静默假成功。
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

ActivityResult = dict[str, Any]

ACTIVITY_REGISTRY: dict[str, Callable[..., Coroutine[Any, Any, ActivityResult]]] = {}
"""注册名 → 实现。键与 contracts 注册表逐字一致（tests/test_activity_registry.py 断言）。"""
