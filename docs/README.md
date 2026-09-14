# 资料索引（docs/）

本项目已查阅的所有文档、资料归档处。队友和后续 Agent 优先从这里找资料。

## 本目录归档

| 文件 | 内容 | 来源 |
|---|---|---|
| [项目企划书.md](项目企划书.md) | **项目唯一基准文档（v0.2 交付版）**：三层能力、功能清单 F1–F26、技术选型 T1–T9、决策记录 D1–D9、变更登记、学习会话设计（附录 A） | 本项目，2026-09-13 |
| [学习会话设计-定稿.md](学习会话设计-定稿.md) | 学习会话（第三层「学会」）设计定稿；**已并入企划书附录 A**，本文保留备查 | 本项目，2026-09-13 |
| [设计资源与原则.md](设计资源与原则.md) | 视觉设计资源与设计原则 | 本项目 |
| [部署指南.md](部署指南.md) | CloudBase 云托管部署步骤（Dockerfile / 环境变量 / 回调地址） | 本项目 |
| [知乎黑客松-技术指南.md](知乎黑客松-技术指南.md) | 官方技术指南全文（环境准备、CLI、OAuth、看山工作台、FAQ、资源汇总） | 飞书云文档，2026-09-13 读取 |

## 仓库内其他资料位置

| 位置 | 内容 |
|---|---|
| `server/static/vendor/katex/` | **本地化的 KaTeX 0.18.7**（公式编译；仅 woff2 字体，约 600KB，MIT。升级方式：从 npm 取 dist，删掉 css 里 woff/ttf 两个 src） |
| `server/static/math.js` | 公式识别（含无分隔符的裸 LaTeX）+ KaTeX 渲染 + 「渲染后 DOM ↔ 原文偏移」映射（划选/标记锚定依赖它） |
| `server/static/preview/math.html` | 公式渲染验证页：30 项断言（识别、DOM 原子序列、逐字符偏移、吸附）+ 可视化对照。用相对路径引资源，可直接双击打开 |
| `skills/zhihu/references/` | **官方知乎 skill 文档**（0.7.2-beta）：hackathon.md（赛程/提交要求）、user-api.md（用户数据 API 契约）、hackathon-oauth.md（OAuth 接入）、http-api.md（搜索/热榜/直答）、hackathon-content-api.md、creator.md、oauth.md 等 |
| `skills/zhihu-v2026s2/` | 官方黑客松 skill 包（v2026s2，来源：https://zhstatic.zhihu.com/skill/zhihu-hackathon-skill_v2026s2.zip ）：初始化编排、OAuth 引导文案、Hello World 模板 + Windows 适配脚本（win_init.mjs / win_finalize.mjs） |
| `skills/distiller-project/` | 本项目 skill：产品定位、架构、三指标口径、api-contracts.md |
| `skills/distiller-pitfalls/` | 踩坑记录（Windows + 沙箱 + 官方工具链的 10 个坑与解法） |
| `probe/probe_favorites.py` | 收藏链路探测脚本（已实测全通） |
| `TODO.md` | 48 小时任务看板 |

## 外部链接

| 用途 | 地址 |
|---|---|
| 赛事活动页（报名/组队/创建项目/提交） | https://www.zhihu.com/hackathon?activity_code=zhihu_hackathon_2026_p2 |
| 知乎数据开放平台（Access Secret 管理） | https://developer.zhihu.com/profile |
| 开放平台文档中心 | https://developer.zhihu.com/docs?key=zhihu_cli |
| 本次项目 GitHub 仓库 | https://github.com/leijiangy/knowledge-distillation-station |
| 看山工作台介绍 | https://www.zhihu.com/parker/campaign/2078900697026905490 |

## 项目凭证与配置位置（不含密钥值）

| 配置项 | 存放位置 | 是否进 git |
|---|---|---|
| OAuth App ID（441） | `server/.env`（`ZHIHU_OAUTH_APP_ID`）；官方脚手架参考 `app/hackathon.config.json` | 是（公开配置） |
| OAuth App Key | `server/.env`（`ZHIHU_OAUTH_APP_KEY`）+ 云托管环境变量 | **否** |
| 开放平台 Access Secret | `server/.env`（`ZHIHU_ACCESS_SECRET`）+ 云托管环境变量 | **否** |
| DeepSeek API Key | `server/.env`（`DEEPSEEK_API_KEY`）+ 云托管环境变量 | **否** |
| CloudBase 数据访问 | 云托管环境变量：`CLOUDBASE_ENV_ID` / `CLOUDBASE_API_KEY`（服务端 API Key） | **否** |
| OAuth 回调地址 | 部署后在云托管环境变量登记 + 赛事页面登记（两处必须完全一致） | 是（地址本身不敏感） |

部署时把 App Key 与 Access Secret 写入部署平台的 Secret/环境变量，不进入代码包。

## 注意

- 本目录只存**公开资料**；用户个人数据（收藏夹内容、账号信息）与任何凭证（Access Secret / App Key / OAuth Token）**不得**写入本仓库任何文件。
- 归档外部文档时注明来源与读取时间；文档更新后以官方在线版本为准。
