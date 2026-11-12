# Changelog

## 1.1.0 - 2026-11-12

- 新增 WebUI 版本状态：检查 GitHub Release / Tag / metadata，区分“已是最新 / 有新版本 / 无法检查”；
- 新增群聊上下文：`off` / `persistent` / `quote` / `recent`；`persistent` 只记录白名单群，默认 50 条（0～200），支持图片识别与 STT 文字化，原文/媒体不落盘；
- 安全审核系统 Prompt 可在 WebUI 中编辑并恢复默认；
- `/council stop` 改为独立注册命令，不再作为议题参数解析；
- 修复数字输入步进导致单次发言/总结上限默认值被浏览器判定非法的问题。

## 1.0.0 - 2026-11-12

首个版本。

- 多 Provider、多人格圆桌讨论；
- 仅 `/council <议题>` 与 `/council stop` 两个指令，`stop` 为独立注册命令；
- 群内只输出总结，完整逐字稿保存到 WebUI；
- WebUI 讨论配置、成员管理、历史查询、删除、导出、统计、运行中取消；
- 默认全开的白名单/黑名单访问控制，黑名单优先；
- native / off / prefetch 三种搜索模式，默认 native；
- 可选 AnySearch 预检索；
- 讨论前 LLM 安全审核，默认 standard、fail-closed；审核系统 Prompt 可在 WebUI 中编辑并恢复默认；
- 群聊上下文：`off` / `quote` / `recent`，默认关闭；`recent` 仅 OneBot，按需读取、不落盘；
- Token 总预算、轮数、上下文、超时、时长、并发、冷却控制；
- 图片/文字输出，图片失败自动降级；
- SQLite 历史存储与保留策略。
