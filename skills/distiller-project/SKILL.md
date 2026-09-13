---
name: distiller-project
description: 知识蒸馏站（知乎黑客松 2026 参赛项目）的项目上下文。任何为这个仓库写代码、改配置、做部署、写计划书或接知乎 API 的任务都先读本 skill；不适用于其他项目。
---

# 知识蒸馏站 项目上下文

## 这是什么项目

知乎黑客松 2026 校园新锐季参赛作品：一个「帮你把收藏的每篇回答真正学会」的在线智能体网站。
核心洞察：用户收藏了很多知乎回答/文章，但「收藏 ≠ 学会」。产品读取用户收藏夹，对每条收藏给出三个指标，帮用户找出「一直想学却没弄懂」的那篇，再逐层教会用户。

## 三层能力（递进，按优先级实现）

1. **第一层（必须今天完成）**：访问用户收藏夹，逐条展示 准确性 / 信息量 / 认可度 三指标 → 用户选出想学没弄懂的那篇
2. **第二层**：用知乎全站搜索接口找同类内容，按指标推荐更高分的回答/文章
3. **第三层（视进度取舍）**：读回答里的论文 / GitHub 链接，讲解给用户

## 硬约束（不可改）

- 提交截止 **9/15 10:00**，提交后不可更改替换 → 9/14 深夜冻结
- 必交：公网可跑 Demo + 产品说明计划书；选交：代码仓库（加分）
- 评审权重：AI 场景价值 40% / 创新度 25% / 完成度 25% / 设计感 10%
- 人气奖（9/13–9/23）：项目广场点赞+使用+评论，且**接入知乎登录的用户数是重要参考**
- 凭证（App Key / Access Secret / OAuth Token）绝不进 git、前端、日志、截图

## 技术架构（已定，勿换栈）

- `app/`：官方脚手架起步的 Node **零依赖** HTTP 服务（`server.mjs` + `lib/oauth.mjs` + `public/`），端口 4173。保持零依赖，部署简单。
- OAuth 流程已由官方模板实现：`/api/oauth/start` → 知乎授权 → `/auth/callback` → token 存服务端内存会话
- 凭证在 `app/.env`（gitignore 已忽略），`server.mjs` 启动时加载；部署平台用其 Secret 能力
- 五项用户接口封装在 `lib/oauth.mjs` 的 `runAll`（contents/followees/favlists/favlist_contents/collections）

## 知乎 API 关键事实（实测确认）

详见 [references/api-contracts.md](references/api-contracts.md)。最重要的：

- 域名 `https://developer.zhihu.com`，鉴权 = `Authorization: Bearer <access_secret>` + `X-OAuth-Token`（OAuth 用户）+ `X-Request-Timestamp`（秒级）
- **取全收藏夹走 `favlists` + `favlist_contents`（有 Offset 分页）**；`collections` 只有近期 Top 50 无分页，仅做快捷入口
- 认可度三指标（LikeCount/CommentCount/FavoriteCount）接口直接返回
- **额度每能力组每日 100 次** → 服务端必须缓存收藏数据（内存 TTL 即可），不能每个访客实时打接口

## 三指标口径（产品核心，别漂移）

- **认可度**：接口三计数（赞/评/藏）归一化，对数缩放
- **准确性**：搜索接口的认证标（AuthorBadge）+ 直答交叉验证（第二层实现时细化）
- **信息量**：摘要长度/结构 + 引用密度启发式起步，后续可换模型

## 目录速览

```
app/                    应用本体（Node 零依赖）
  .env                  凭证（不入库）
  hackathon.config.json 项目配置（App ID 441）
  lib/oauth.mjs         OAuth + 五项用户接口
skills/                 各类 skill 包（官方 + 自建 + Windows 适配脚本）
probe/                  接口探测脚本（probe_favorites.py，已验证全通）
TODO.md                 48 小时任务看板
```

## 待办里程碑

- 今天：第一层功能 + 部署公网 + 配 OAuth 回调 + 发想法帖
- 9/14：第二层 + 计划书初稿 + Demo 链接进想法帖
- 9/14 深夜：冻结
- 9/15 上午：只做缓冲和提交
