# 知乎开放平台 API 契约（实测确认 2026-09-13）

完整文档在 `app/.codex/skills/zhihu/references/`（官方 skill 包），本文件只记录已实测确认和对本项目关键的部分。

## 用户数据接口（主链路）

基础域名 `https://developer.zhihu.com`，全部 GET。

| 接口 | 路径 | 分页 | 用途 |
|---|---|---|---|
| 收藏夹列表 | `/api/v1/user/favlists` | 无（Limit≤50，服务端忽略 Offset） | 列出用户收藏夹 |
| 收藏夹内容 | `/api/v1/user/favlist_contents` | **有**（Offset/Limit≤50 + Paging.IsEnd/NextOffset） | 取全指定收藏夹 |
| 近期收藏 | `/api/v1/user/collections` | **无**（仅 Top 50） | 快捷入口，不承载全量承诺 |
| 创作列表 | `/api/v1/user/contents` | 有 | 备用 |
| 关注列表 | `/api/v1/user/followees` | 有 | 备用 |

`favlist_contents` 必传 `FavlistUrlToken`（从 favlists 的 `UrlToken` 取）。

## 请求头（三件套，缺一不可）

```
Authorization: Bearer <access_secret>
X-OAuth-Token: <oauth_token>          ← 代表授权用户时；本人查询不传
X-Request-Timestamp: <unix秒>
Content-Type: application/json
```

## 响应外层与错误码

```json
{"Code": 0, "Message": "success", "Data": {"Items": [...], "Paging": {...}}}
```

错误码：10001 参数错 / 20001 鉴权失败 / 30001 频率限制 / 30002 配额限制 / 90001 内部错误。
注意 `Paging.NextOffset` 是 **String**，回传前要 int 解析，失败即停不静默截断。

## CollectionContentItem 字段（已实测返回）

`ContentType, Url, CreatedAt, FavTime, LikeCount, CommentCount, FavoriteCount, Title, Summary, Favlists, Author`

- ContentType 小写：answer / article / zvideo / pin / question
- FavTime 收藏时间秒级时间戳；Favlists 是该内容所在的收藏夹数组
- Author 可能缺失（下游未返回时不输出）

## 实测结论（2026-09-13，用户本人账号 + Python 后端）

- favlists 返回 1 个收藏夹（含私密夹，**私密收藏夹本人 API 可读**）
- favlist_contents 一页 49 条即 IsEnd=True，分页协议正常
- `/api/collections`（Python 版）全量返回 49 条 + 三指标，缓存命中验证通过
- **OAuth 真实登录待部署后验收**（本地无公网回调，官方明令本地地址不可作回调）
- 已知实现细节：
  - httpx 必须 `trust_env=False`（环境 SSL_CERT_FILE 污染，见 distiller-pitfalls 第 11 条）
  - `Paging.NextOffset` 是 String，转 int 失败要停止而非猜测
  - `/user` 的 `uid` 是 int64，Python 无精度问题（JS 需注意）

## 搜索 / 直答（第二层用）

- 知乎搜索：`GET /api/v1/content/zhihu_search`（Query 必填，Count≤20）
- 返回 Item 含 `VoteUpCount/CommentCount/AuthorName/AuthorBadge`（认证标）——推荐排序和「准确性」指标的原料
- 直答：CLI `answer` 或 HTTP（见官方 http-api.md），支持流式

## 额度（影响架构的硬数字）

- 每能力组每自然日 100 次（未实名 10 次）；`quota` 查询不消耗额度
- 「用户数据」是一个能力组：favlists/favlist_contents/collections 共享 100 次/天
- **结论：服务端必须缓存**。建议收藏数据缓存 TTL ≥ 10 分钟；多人访问 Demo 时共享缓存。

## OAuth（已配置，等部署验收）

- App ID `441`；授权 URL `https://openapi.zhihu.com/authorize?redirect_uri=...&app_id=...&response_type=code&state=...`
- Token 交换：POST `https://openapi.zhihu.com/access_token`（app_id/app_key/grant_type/redirect_uri/code 表单）
- 回调必须是公网 HTTPS 且以 `/auth/callback` 结尾，与赛事页面登记值**完全一致**
- state 透传已支持；实测可能不回传 state，此时只能标记「联调可用」不得声称生产安全
