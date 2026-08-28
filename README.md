# ishome-reportgen

《是我的家》报告成文线服务（`reportgen-svc`）：独立部署的 Temporal worker，承接报告**成文线**（gen-generated/gen-polished）——各 dom- 单元并行成文 → 页面装配 → 册级校验（《装修报告AgentGraph设计说明》v0.2 §2/§3）。

- **出处**：《装修报告生成规则规范》v2.2 + 图 v0.2 §8——求值线落 project-svc 规则引擎（Java，同步），成文线落本仓；编排=genpipe workflow。
- **task queue**：`reportgen-activities`（namespace `genpipe`；注册表：ishome-contracts `registries/task_queues.md`）。
- **本仓 activity**：成文线首批 activity **待注册**（注册名唯一真源：ishome-contracts `activities/registry.md`，只增不改；名字须先过 contracts PR 评审再落实现——规则 1.7 语义命名）。
- **硬性纪律**（图 v0.2 §0，实现时逐条兑现）：
  - 输入=报告数据包（input_snapshot，自包含）：**不回查任何库**，数字字段只能引用落点对象（求值线产出），成文线不产生任何数字；
  - **生成侧不知用户是谁**：输入是匿名结构，无任何用户标识；
  - persona/句式/判据/反例库全部是 release 数据随包下发，**运行时不读任何人写的文本**（规则 4.19）;
    persona 四件（规则 4.13）在本仓的消费面已齐——①身份语域=prompt 头、②好/坏对照句对=prompt 示范块
    （`writer.judgment_pairs`，`reason` 的 cr- 编号不下发给写作器）、③断言预算切"许说/不许说"两张清单、
    ④禁词表双消费（prompt 约束 + 机检扫描，规则 4.15）；
  - **锁定文案零生成**（规则 2.4 gen-locked）：必挂 ID 随包下发（`lockedTextsByDomain`，枚举见
    contracts `registries/locked_texts.md`），单元只透传、**装配层挂上页**、册级验齐
    （`gate-locked-text-missing`）——写作器全程看不到 ID，`Card` 上也没有它的位置；
  - 出口过检不合格重写 ≤2 轮，仍不合格 → failed verdict 上抛，**绝不静默假成功**；
  - 一台引擎挂 N 个域资产包，**禁止按域拆 activity/服务**（规则 5.0c）。
- **出口过检两道**（图 v0.2 §3，都在 `report-unit-compose` 内，判官不另注册 activity）：
  - 规则层 `gate.py`：确定性机检（占位符/裸数字/禁词/pattern 判据），命中即拦；
  - 判官层 `judge.py`：语义判据（规则层判不出的那类），按包内 cr- 判据挂着的反例样例读文稿，
    **只报 cr- 编号不改写**；拦不拦由数据侧 `status` 决定（`observing` 只记录不拦截 = 规则 4.17
    入册门禁第二道，首批一律此档），**判官挂掉不阻塞成文**——第二道不可用只是没有观察数据。

## 常用命令

```bash
uv sync                  # 安装依赖与 dev 工具
uv run ruff check .      # lint
uv run lint-imports      # import 方向契约（worker → activities → models 单向）
uv run mypy              # strict 类型检查
uv run pytest            # 测试（activity 注册名守门）
uv run reportgen-worker  # 起 worker（TEMPORAL_ADDRESS，默认 localhost:7233）
```

新 clone 后执行一次：`git config core.hooksPath .githooks`（本地 pre-push 质量门）。
