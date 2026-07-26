# Robocat（机器猫）

CATCRAFT: Reborn（Кошкокрафт）Minecraft 社区的 Discord 机器人——新闻、AI 助手、24/7 电台、工单与管理，集于一身。

[English](README.md) · [Русский](README.ru.md) · 中文

[![CI/CD](https://github.com/szarkans/robocat/actions/workflows/deploy.yml/badge.svg)](https://github.com/szarkans/robocat/actions)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)

## 功能

**新闻**

- `/news` — 新闻编辑器：弹窗表单、临时预览、以富组件（Components V2）形式发布；`/news_edit` 可重新打开已发布的帖子进行修改。
- `/digest` — 由 LLM 生成的近期新闻摘要。
- VK 转发 — 人工审核门：编辑一键将已发布的新闻（含媒体）转发到 VK 社区。

**AI 助手**

- 在频道中被 @ 时回答问题；通过 `/aichat` 开启私密 AI 线程，并在后台压缩对话历史。
- 通过向量嵌入搜索服务器 Wiki，必要时使用免费网页搜索（SearXNG 或 DuckDuckGo）。
- 记住玩家相关的事实，通过 ffmpeg 抽帧“观看”视频，并按来源统计 token 消耗（`/aistats`）。

**CatCraft FM**

- 语音频道中的 24/7 电台：支持投票切换上一首/下一首，监督进程可在断线和重启后自动恢复。

**管理与工单**

- 管理员工单和带频率限制的 Bug 报告。
- 处罚管理、自动禁言垃圾机器人的蜜罐频道、身份组选择、玩家查询。

**集成**

- 向 Minecraft 服务器发送 RCON 命令。
- VK（新闻转发）、SearXNG/DuckDuckGo（网页搜索）。

## 工作原理

单个 `disnake` 机器人进程；每个功能都是在 `main.py` 中加载的 cog。

- **存储** — 通过 `aiosqlite` 使用 SQLite。核心原语是旗标（flag）系统：可以把任意键值对挂到任何 Discord 实体（用户、频道、消息或抽象实体）上，既存放功能数据，也存放运行时配置。
- **LLM 层** — 所有模型调用都经过唯一入口 `bot/ai/llm.py`。提供方为 OpenAI 兼容 API（Gemini、Groq、OpenRouter 等），在 `data/ai_settings.yaml` 中配置；该层在出错时按供应商轮换并设置冷却时间，同时统计 token。
- **配置** — Discord 的各类 ID（频道、身份组、用户）保存在 `data/discord.local.yml` 中，故意不纳入 git：运维直接在服务器上修改，并通过 `/config-reload` 热加载，无需重启。

## 部署

全自动，无需手动操作：

1. 推送到 `master`。
2. GitHub Actions 运行 pytest 检查。
3. 检查通过后，工作流通过 SSH 部署到 VDS：安装依赖、按 SHA 检出对应提交、运行 `data/db_init.py`。
4. 重启 systemd 服务 `robocat`，随后进行健康检查。

回滚 = 一次 revert 提交，走同一条流水线。

## 社区

CATCRAFT: Reborn（Кошкокрафт）— 俄语 Minecraft 社区。

- Discord：[discord.gg/catcraftmc](https://discord.gg/catcraftmc)（直接邀请链接：[discord.gg/6f3FwFRJWC](https://discord.gg/6f3FwFRJWC)）
- Telegram：[t.me/catcraftmc_tg](https://t.me/catcraftmc_tg)

## 许可证

AGPL-3.0 — 见 [LICENSE](LICENSE)。衍生作品（包括以网络服务形式部署的）必须以相同许可证发布。
