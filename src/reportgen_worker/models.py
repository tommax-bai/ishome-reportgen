"""reportgen_worker activity 出入参模型（pydantic）。

成文线输入契约 = **报告数据包**（input_snapshot，图 v0.2 §2）：落点对象（值/区间/
依据 release 引用/管的时刻/生活翻译）+ 锁定清单 + 动作表 + 匿名画像 + persona
release 引用。该结构由求值线（project-svc 规则引擎）产出并随任务下发，schema
随求值线首实装定形后入 contracts；此前本模块不预造字段。

跨 domain 纪律：worker 不 import 其他 domain 的内部模块；成文线**不回查任何库**，
数字字段只能引用落点对象（图 v0.2 §0/§3）。
"""

from __future__ import annotations
