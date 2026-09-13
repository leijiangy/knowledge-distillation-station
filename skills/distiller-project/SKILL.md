---
name: distiller-project
description: 知识蒸馏站（知乎黑客松 2026 参赛项目）的项目上下文。任何为这个仓库写代码、改配置、做部署、写计划书或接知乎 API 的任务都先读本 skill；不适用于其他项目。
---

# 知识蒸馏站 项目上下文

## 先读基准文档

**`docs/项目企划书.md` 是项目唯一基准**：功能范围、优先级、技术选型、决策记录、变更规则全在里面。本 skill 是快速导航，两者冲突时以企划书为准；改方向先在企划书登记再动代码。

## 这是什么项目

知乎黑客松 2026 校园新锐季参赛作品：连接用户的知乎收藏夹，把它从「知识坟场」变成学习进度表。
核心洞察：收藏 ≠ 学会。产品读完收藏夹，对每条内容给出三指标，帮用户找到真正值得花时间学的那篇。

赛道：知识炼金场（社区 × 学习）。提交截止 9/15 10:00，不可更改替换。

## 当前阶段范围（9/13 收窄后）

- ✅ **在做**：「看见」闭环——登录 → 收藏全量读取 → 三指标卡片 → 排序（已完成本地版）
- ⏸️ **暂停待研究**：学习会话（补共识/拆逻辑）、同类替换——等学习方式文献研究完成后重新设计
- 🚫 **已取消**：「我一直没弄懂」手动标记（F7）

## 技术架构（Python 版，勿换栈）

```
浏览器（原生 HTML/CSS/JS，无构建）
   └─ server/main.py（FastAPI + uvicorn）
        ├─ /api/oauth/*      登录、回调、会话（core/oauth.py）
        ├─ /api/favlists     收藏夹列表
        ├─ /api/collections  收藏全量分页读取 + 三指标（core/zhihu.py + analyze.py）
        └─ 两级缓存（core/cache.py）：内容键全站共享 + 用户键私有，**TTL 均 1 天**（团队决策：额度优先；用户点「刷新」传 force=1 绕过用户缓存）。favlists 与 collections 均已接入缓存
```

**安全红线（P0 修复，不得回退）**：
- `ALLOW_SELF_MODE` 默认关闭；仅本地 `.env` 显式设为 1 才允许未登录请求以本人身份读数据。**公网部署绝不设置该变量**，否则任何访客可读到项目账号的私密收藏。
- `/api/favlists`、`/api/collections` 在未登录且开关关闭时一律返回 `LOGIN_REQUIRED`。
- OAuth 回调 state 校验用 `core/oauth.py:check_state`（四态：missing/mismatch 拒绝，verified 通过，unverified 容忍但标记），有测试覆盖。

- 依赖仅 fastapi / uvicorn / httpx（requirements.txt）；测试用 pytest
- AI 能力用 DeepSeek（OpenAI 兼容接口，密钥 `DEEPSEEK_API_KEY`）——学习会话接入时使用
- 凭证在 `server/.env`（gitignore 已忽略）；部署用 CloudBase 环境变量
- **httpx 必须 `trust_env=False`**：环境里的 SSL_CERT_FILE 会污染公网站点 TLS 验证（详见 distiller-pitfalls）

## 知乎 API 关键事实（实测确认）

详见 [references/api-contracts.md](references/api-contracts.md)。最重要的：

- 域名 `https://developer.zhihu.com`；用户数据接口 = `Bearer <Access Secret>` + `X-OAuth-Token`（代表授权用户）+ `X-Request-Timestamp`
- `https://openapi.zhihu.com/user` 例外：**只带 `Bearer <oauth_token>`**（官方 profile 文档明确；code 20000 也是成功）
- **取全收藏夹走 favlists + favlist_contents**（有 Offset 分页）；collections 只有近期 Top 50 无分页
- 额度每能力组每日 100 次 → 服务端必须缓存（已实现）

## 三指标口径（产品核心，别漂移）

- **认可度**：赞/评/藏三计数对数缩放加权（收藏 0.5 / 赞同 0.3 / 评论 0.2）
- **信息量**：摘要长度 + 结构信号 + 引用密度启发式
- **准确性（v1）**：公开信号代理（作者完整度/内容类型/外部引用），basis 透明列出；完整版接搜索接口认证标交叉
- **三个指标分开展示，不合成综合分**（团队决策 D2）

## 目录速览

```
server/                  Python 应用（FastAPI）
  main.py                入口 + 路由
  core/                  config / oauth / zhihu / cache / analyze / sessions
  static/                前端（index.html + app.js + style.css）
  tests/                 pytest
  .env                   凭证（不入库）
app/                     官方 Node 脚手架（OAuth 协议参考实现，已归档）
docs/                    企划书（基准）+ 资料归档 + 技术指南存档
skills/                  官方 skill 包 + distiller-project / distiller-pitfalls
probe/                   接口探测脚本（probe_favorites.py 已验证）
TODO.md                  48 小时任务看板
```

## 当前进度与里程碑

| 时间 | 目标 | 状态 |
|---|---|---|
| 9/13 | 企划书确定 + 「看见」闭环本地可用 + 部署材料 | ✅ 基本完成 |
| 9/13 晚–9/14 | 学习方式文献研究（D1） | ⏳ |
| 9/14 | CloudBase 部署 + OAuth 回调 + 真实登录验收 | ⏳ |
| 9/14 深夜 | 内部冻结（链接可跑、测试账号可用、计划书定稿） | ⏳ |
| 9/15 上午 | 只留缓冲，9:00 前提交完毕 | ⏳ |
