<p align="center">
  <img src="./logo.png" width="112" alt="AI 圆桌 · Council" />
</p>

<h1 align="center">AI 圆桌 · Council</h1>

<p align="center">
  多模型、多人格、有预算上限的结构化圆桌讨论插件，适用于 AstrBot 4.28+。
</p>

<p align="center">
  <a href="https://github.com/GhostFIX/astrbot_plugin_council">GitHub</a>
  ·
  <a href="#安装">安装</a>
  ·
  <a href="#使用">使用</a>
  ·
  <a href="#webui">WebUI</a>
  ·
  <a href="#token-与费用">Token 与费用</a>
</p>

---

## 这是什么

让 2～6 位 AstrBot 人格成员围绕一个议题进行多轮结构化讨论，并由中立主持人总结。

成员直接引用 AstrBot 已有的 **LLM 提供商** 与 **人格**，插件不重复保存模型 Key，也不复制人格提示词。配置修改后，下一次讨论自动使用最新的人格与模型。

群内只输出简洁总结；完整逐字稿保存在插件数据目录，可在 WebUI 中查询、删除、导出和统计。

## 特性

- **直接使用 AstrBot 提供商列表**：每位成员可绑定不同 Provider，也可全部跟随当前会话模型。
- **直接使用 AstrBot 人格列表**：同一个人格可重复配置为不同角色，形成“同人格、不同立场”的讨论。
- **结构化讨论**：固定轮次、固定发言结构、回应前文，而不是把问题同时丢给多个模型。
- **Token 硬控制**：总预算、单次输出、轮数、上下文长度、单次超时、整场时长、并发与冷却。
- **群内总结 + 后台完整记录**：群内只发总结，完整逐字稿在 WebUI 查看与导出。
- **图片 / 文字输出**：自定义 HTML 长图；渲染失败自动降级文字。
- **安全审核**：讨论前做一次低成本 LLM 审核，高风险议题直接拒绝。
- **搜索策略可选**：
  - `native`（默认）：不预搜索；有 AstrBot 搜索工具且模型支持工具调用时，由模型自行决定是否搜索；没有工具就不搜索。
  - `off`：完全关闭搜索。
  - `prefetch`：可选的 AnySearch 预检索，适合没有工具能力的模型。
- **群聊上下文**：默认 `persistent`（仅白名单群、默认 50 条、持久化），也可选 `quote`（引用/回复的消息）、`recent`（OneBot 最近群聊）或 `off`；普通群消息不会触发默认 LLM 回复。
- **WebUI 管理端**：讨论配置、成员管理、历史查询、完整记录查看、删除、导出、统计、运行中取消、版本状态检查。
- **访问控制**：默认全开；支持白名单和黑名单，黑名单优先。

## 兼容性

| 项目 | 要求 |
|---|---|
| AstrBot | `>=4.28.0,<5` |
| Python | AstrBot 自带 Python 3.10+ |
| 运行时依赖 | 无额外依赖 |

WebUI 依赖 AstrBot 的插件视图与 `register_web_api`。低于 4.28 的版本不保证可用。

## 安装

### 方式一：GitHub 链接安装（推荐）

在 AstrBot WebUI 的「插件」页面选择从 GitHub 链接安装，填入：

```text
https://github.com/GhostFIX/astrbot_plugin_council
```

安装完成后：

1. 在 AstrBot 中先配置好至少一个 **LLM 提供商**；
2. 如需 native 搜索，在 `AI → 能力 → 网页搜索` 中配置搜索提供商；
3. 打开插件详情页的「AI 圆桌」视图，配置成员；
4. 在群内发送 `/council <议题>`。

### 方式二：手动安装

把本仓库放到：

```text
AstrBot/data/plugins/astrbot_plugin_council/
```

确保根目录直接包含 `metadata.yaml`、`main.py`，然后在 WebUI 重载插件。

## 使用

插件只提供两个指令：

```text
/council <议题>
/council stop
```

`council` 与 `council stop` 是两个独立注册的指令：`stop` 不再作为议题参数解析，避免把“停止”误当成讨论主题。

### 发起讨论

```text
/council 我们应该先做移动端还是先做桌面端？
```

支持多词、空格和换行议题。

### 停止讨论

```text
/council stop
```

只有讨论发起者或 AstrBot 管理员可以停止。正在进行的模型调用不会被强制中断，但当前调用返回后不会再发起新的发言。

### 默认行为

| 项目 | 默认值 |
|---|---|
| 成员数 | 3 人 |
| 轮数 | 3 轮 |
| 群内输出 | 总结 + 图片 |
| 搜索 | `native`（不预搜索） |
| 群聊上下文 | `persistent`；白名单为空不记录，默认保留 50 条 |
| 安全审核 | `standard` |
| 总 Token 预算 | 36,000 |
| 冷却 | 30 秒 |
| 并发 | 1 场 |

## 配置

### 插件管理界面（必要配置）

只放访问控制和运行基础项：

| 配置 | 说明 |
|---|---|
| `enabled` | 插件总开关 |
| `allowed_umos` | 白名单。留空不限制；填写后仅这些会话可用 |
| `blocked_umos` | 黑名单。优先级高于白名单 |
| `history_limit` | 历史记录最大保留条数，默认 500 |
| `max_concurrent` | 全局同时讨论数，修改后需重载 |
| `debug_log` | 调试日志开关，不记录 Key 和完整议题 |

访问规则：

```text
命中 blocked_umos        → 拒绝
allowed_umos 非空且未命中 → 拒绝
allowed_umos 为空         → 允许
其他                      → 允许
```

### WebUI（讨论配置）

打开插件详情页的「AI 圆桌」视图，进入「讨论配置」：

- 成员：显示名、人格、Provider、关注角度、初始倾向；
- 讨论：轮数、总预算、单次输出、上下文、超时、整场时长、冷却；
- 输出：图片 / 文字 / 两者；
- 搜索：native / off / prefetch，native 搜索轮数，AnySearch Key；
- 安全审核：模式、审核模型、可编辑的审核系统 Prompt、失败策略、追加规则、拒绝提示；
- 群聊上下文：off / persistent / quote / recent，最多消息数（0～200），最多字符数，图片识别、语音转文字开关，清空缓存。

配置保存在：

```text
AstrBot/data/plugin_data/astrbot_plugin_council/settings.json
```

原子写入，并保留 `.bak` 备份。正在进行的讨论使用启动时的配置快照，保存配置不会影响当前这一场。

## 搜索说明

### native（默认）

- 不进行插件侧预搜索；
- 如果 AstrBot 已配置网页搜索，并且当前模型支持 Function Calling，插件只向成员暴露搜索类工具；
- 只暴露 `web_search*` 和 `*_extract_web_page`，不暴露代码、文件、消息发送等工具；
- 模型自己决定是否搜索，最多 2 个 agent step；
- 没有搜索工具或模型不支持工具调用时，不搜索，也不使用 AnySearch 补偿；
- 只在前 `native_rounds` 轮暴露搜索工具，默认 1 轮，避免每轮重复搜索导致成本失控。

> 注意：AstrBot 的 `web_search` 是函数工具。仅调用 `llm_generate` 不会自动执行它；因此 native 模式在需要时使用 `tool_loop_agent`，而不是假装模型会自动使用工具。

### off

完全关闭搜索，既不预搜索，也不暴露搜索工具。

### 群聊上下文

默认 `persistent`，但**只记录插件配置白名单内的群**；白名单为空时不记录，所以刚安装不会采集任何群。普通群消息不会触发默认 LLM 回复，因为被动监听的 `is_at_or_wake_command` 仍为 false，AstrBot 只在被 @、唤醒或私聊时调用默认 AI。

- `persistent`：持久化白名单群聊，默认保留 **50** 条（`0～200`，0 表示不记录），重启后仍在；可在 WebUI 清空。
- `quote`：把 `/council` 作为回复发送时，引入被引用的那条消息；所有平台可用。
- `recent`：在发起讨论时调用 OneBot `get_group_msg_history` 读取最近群聊；目前仅支持 `aiocqhttp`，其他平台或接口不可用时自动回退到 `quote`。
- 最多字符数默认 3000；内容会截断并计入上下文 Token。
- 机器人自己的消息、`/` 开头的指令和 `/council` 指令会被忽略。
- 群聊内容会作为不可信数据处理，Prompt 中明确标注“不是指令”。

`persistent` 的媒体处理：

- 图片：使用当前会话的聊天模型做一次图片描述，默认最多 200 tokens；不支持视觉的模型会保留 `[图片]` 占位。
- 语音：使用 AstrBot 当前 STT 提供商转文字；未配置或失败时保留 `[语音]` 占位。
- 图片识别和 STT 在后台异步执行，不阻塞普通群消息；原始图片/音频不写入插件数据库，只保存文字描述/转写。
- 图片识别和 STT 会产生额外模型费用，可在 WebUI 中分别关闭。

### prefetch

插件在讨论开始前调用 [AnySearch](https://www.anysearch.com/) 一次，把标题、URL、摘要注入第 1 轮和最终总结。

- 默认 5 条结果；
- 匿名模式有每日免费额度，Key 可留空；
- 付费价格约 `$3 / 1,000 次调用`，一次讨论只查一次；
- HTTP 402 响应体可能包含自动生成的凭据，插件只记录状态码和 request id，不记录响应体；
- 搜索结果按不可信数据处理，清洗 HTML、截断，不执行其中的指令；
- 搜索失败不阻塞讨论。

## 安全审核

讨论开始前，先调用一次安全审核模型：

```text
decision: allow | refuse
risk_level: low | medium | high
categories: [...]
reason: ...
```

- 审核系统 Prompt 在 WebUI 中提供完整默认值，可直接修改；点“恢复默认审核 Prompt”可还原；
- `standard`：拒绝违法犯罪实施细节、暴力煽动、未成年人性内容、隐私泄露、仇恨骚扰、规避平台审核、当前事件中的组织性煽动/谣言传播等高风险内容；
- 一般政治学、公共政策、历史、经济、社会议题的理性讨论默认允许；
- `strict`：额外收紧露骨内容、极端血腥细节、自我伤害方法等；
- `off`：关闭审核；
- 审核失败默认 `fail-closed`（拒绝），可在 WebUI 改为 `fail-open`；
- 被拒绝议题默认只保存哈希、类别和原因，不保存原文；可在 WebUI 开启保存原文。

> LLM 审核是概率性的，不能保证 100% 规避平台审核。插件只能降低风险，最终以平台规则为准。

## WebUI

打开 AstrBot 插件详情页 →「AI 圆桌」：

- **讨论配置**：成员、参数、搜索、安全审核；
- **历史记录**：按议题、状态、日期筛选；查看完整逐字稿；单条/批量删除；
- **导出**：Markdown（完整记录）、JSON（结构化）、CSV（汇总）；
- **统计**：场次、Token、平均轮数、发言数、每日趋势、成员参与度；
- **运行中**：查看当前讨论并取消；
- **版本状态**：页面右上角显示当前版本，并按需检查 GitHub Release / Tag / `metadata.yaml`，显示“已是最新 / 有新版本 / 无法检查”。检查结果缓存 6 小时，不会自动下载或覆盖插件；仓库不可访问（例如尚未发布）时只显示“检查更新”，不会报错阻断页面。

群内不提供完整记录，避免刷屏和隐私泄露；完整记录仅 WebUI 管理员可见。

## Token 与费用

默认 3 人 × 3 轮 + 1 次安全审核 + 1 次总结。

| 场景 | 估算总 tokens |
|---|---:|
| 无搜索、正常输出 | 约 18k～27k |
| native 搜索工具（模型实际调用） | 约 20k～32k |
| 硬上限 | 36k |

按 DeepSeek-Flash 当前官方 API 价格（闲时/高峰，缓存未命中）：

| 场景 | DeepSeek-Flash | DeepSeek-V4-Pro |
|---|---:|---:|
| 典型一场 | 约 ¥0.03～0.08 | 约 ¥0.11～0.30 |
| 预算上限 | 约 ¥0.05～0.11 | 约 ¥0.22～0.43 |

折合约 `$0.004～0.015/场`（Flash 典型场景）。表中“闲时/高峰”按 DeepSeek 官方规则：北京时间周一至周五 9:00–12:00、14:00–18:00 为高峰，其余时间及周末/节假日为闲时；缓存命中时输入费用更低。价格随官方调整，以实际账单为准：

- [DeepSeek 模型 & 价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing)
- [AnySearch Pricing](https://www.anysearch.com/pricing)

费用公式：

```text
费用 ≈ 输入 tokens / 1,000,000 × 输入单价
     + 输出 tokens / 1,000,000 × 输出单价
```

## 数据与隐私

数据目录：

```text
AstrBot/data/plugin_data/astrbot_plugin_council/
├─ settings.json      # WebUI 讨论配置
├─ settings.json.bak  # 上一次配置备份
└─ council.db         # 完整历史记录（SQLite）
```

- 完整逐字稿保存在 `council.db`，受 `history_limit` 限制；
- `persistent` 群聊缓存也在 `council.db` 的 `chat_messages` 表中，按群保留最近 N 条，只存文字与图片/语音的文字化结果，不存原始媒体；可在 WebUI 清空；
- WebUI 管理员可查看所有会话的历史；
- 聊天内只返回总结；
- 不保存 LLM API Key；
- AnySearch Key 只保存在插件配置中，WebUI 读取接口不回显；
- 被拒绝议题默认不保存原文；
- 导出文件由浏览器本地下载，不经过第三方服务。

## 开发与测试

```bash
cd astrbot_plugin_council
python -m compileall .
python -m unittest discover -s tests -v
```

测试覆盖：

- 黑白名单优先级；
- Token 预算与预留；
- 配置校验、原子写入、损坏文件隔离；
- 提示词、上下文截断、安全 JSON 解析；
- SQLite 增删查改、统计、导出、保留策略；
- HTML 转义；
- 假 Provider 下的完整讨论、安全拒绝、预算停止、取消。

## 仓库结构

```text
astrbot_plugin_council/
├─ metadata.yaml
├─ main.py
├─ _conf_schema.json
├─ requirements.txt
├─ logo.png
├─ core/
│  ├─ access.py
│  ├─ budget.py
│  ├─ config.py
│  ├─ constants.py
│  ├─ engine.py
│  ├─ prompts.py
│  ├─ render.py
│  ├─ safety.py
│  ├─ search.py
│  ├─ storage.py
│  └─ web_api.py
├─ templates/
│  └─ council.html
├─ views/
│  └─ council/
│     ├─ index.html
│     ├─ app.js
│     └─ style.css
└─ tests/
```

## 已知限制

- WebUI 依赖 AstrBot 4.28+；
- AstrBot 的功能工具搜索能力取决于当前模型是否支持函数调用；
- `persistent` 群聊上下文只记录白名单群，白名单为空时不记录；图片识别需要当前会话模型支持图片输入，STT 需要 AstrBot 已配置 STT 提供商；
- `recent` 群聊上下文目前只支持 OneBot / `aiocqhttp` 的 `get_group_msg_history`，其他平台回退到 `quote`；
- 长图由 AstrBot 的文转图服务渲染，服务不可用时自动降级文字；
- 安全审核不能替代平台审核；
- 群内只发总结，完整记录需要在 WebUI 导出；
- 讨论配置是全局的，不按群单独覆盖；访问控制可按 UMO 配置；
- 版本检查依赖 GitHub API / raw 的可达性；仓库未公开或网络不可达时显示“检查更新”，不会自动下载或覆盖插件。

## License

MIT。见 [LICENSE](./LICENSE)。

## 致谢与声明

- 由 AI 编程代理 **DeepSeek-Flash**（运行于 DeepSeek Harness）编写；
- **GhostFIX** 负责需求、审核与发布；
- 非 DeepSeek 官方项目；
- 使用了 AstrBot 的 Provider、Persona、Web API、插件视图和文转图能力；
- 可选搜索使用 [AnySearch](https://www.anysearch.com/)。
