# Changelog

## 1.0.0 - 2026-11-12

首个版本。

- 多 Provider、多人格圆桌讨论；
- 仅 `/council <议题>` 与 `/council stop` 两个指令；
- 群内只输出总结，完整逐字稿保存到 WebUI；
- WebUI 讨论配置、成员管理、历史查询、删除、导出、统计、运行中取消；
- 默认全开的白名单/黑名单访问控制，黑名单优先；
- native / off / prefetch 三种搜索模式，默认 native；
- 可选 AnySearch 预检索；
- 讨论前 LLM 安全审核，默认 standard、fail-closed；
- Token 总预算、轮数、上下文、超时、时长、并发、冷却控制；
- 图片/文字输出，图片失败自动降级；
- SQLite 历史存储与保留策略。
