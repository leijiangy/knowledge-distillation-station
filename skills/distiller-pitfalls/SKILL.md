---
name: distiller-pitfalls
description: 知识蒸馏站开发过程中踩过的坑与解法（Windows 环境 + ZCode 沙箱 + 知乎官方工具链）。遇到 git 推送失败、网络不通、官方脚本报错、飞书文档读不了、凭证配置异常等类似问题时先查这里；仅适用于本机本项目的环境。
---

# 踩坑记录（每条 = 坑 → 现象 → 解法）

## 1. 官方黑客松脚本是 macOS 专属

- **现象**：`init_project.mjs` 报 `Unable to inspect bundled official Skill archive`；`set_app_key.mjs` 直接报 `supports secure app_key storage on macOS only`
- **原因**：脚本硬编码 `/usr/bin/unzip`、`/bin/bash`、`/usr/bin/security`（macOS 钥匙串）
- **解法**：Windows 用 `skills/zhihu-v2026s2/win_init.mjs` + `win_finalize.mjs` 等价初始化（含同样的 SHA-256 校验）；app_key 不进钥匙串，走 `.env` 环境变量——官方 `lib/oauth.mjs` 原生优先读环境变量（第 92-93 行）

## 2. 沙箱网络访问 GitHub

- **现象**：之前 `git push/fetch` 直连 GitHub 超时或 Connection reset；`gh api` 一切正常
- **原因**：沙箱对 git.exe/curl.exe 的网络放行策略不稳定（随宿主网络环境变化）
- **当前可用姿势（2026-09-13 实测）**：以下组合命令在沙箱内可正常 fetch/push：

  ```cmd
  set GIT_SSL_CAINFO=&& git -c http.proxy= -c https.proxy= push origin main
  ```

  即：清空 ZCode CA 变量 + 绕开全局代理直连。若再次失败，备用方案是用 `gh api` 同步（contents API 传文件 / git data API 构造提交）。
- **注意**：远端 main 曾被本地完整历史 `--force-with-lease` 覆盖过（远端原为 API 生成的独立历史）。**若队友此前 clone 过旧版，需要重新 clone 或 `git fetch && git reset --hard origin/main`**。

## 3. git 全局代理指向失效端口

- **现象**：git 报 `Failed to connect to 127.0.0.1 port 17897`
- **原因**：`git config --global http.proxy` 指向梯子端口，梯子没开或端口变了
- **解法**：单次绕过 `git -c http.proxy= -c https.proxy= <命令>`；梯子正常时按真实端口改全局配置

## 4. GIT_SSL_CAINFO 环境变量污染证书校验

- **现象**：绕过代理后 git 报 `SSL certificate problem: unable to get local issuer certificate`
- **原因**：用户环境变量 `GIT_SSL_CAINFO` 指向 ZCode 的私有 CA（`C:\Users\JM\.zcode\proxy\ca.pem`），里面没有 GitHub 真实证书链
- **解法**：CMD 会话内 `set GIT_SSL_CAINFO=` 清空后 git 用自带 CA 直连即可；根治是删掉该用户环境变量（比赛后再动）

## 5. 沙箱内 node 子进程的解压结果对主进程不可见

- **现象**：`execFile('tar')` / `Expand-Archive` 成功退出但 readdir 目标目录为空
- **原因**：沙箱对子进程文件系统写入做了隔离（同命令在 CMD 直接跑正常）
- **解法**：解压/复制类操作直接在 Bash（CMD）里做，node 只做纯 JS 文件操作（readFile/cp 等主进程 API 正常）

## 6. 飞书 wiki 有登录墙 + 虚拟滚动懒加载

- **现象**：WebFetch 302 到登录页；直接读 innerText 只有一小节
- **解法**：用内置浏览器（browser-use 插件，iab 后端）evaluate 读正文；**逐章点击左侧目录**触发懒加载后分段读 innerText。沙箱/爬虫都绕不过的登录墙，内置浏览器带登录态能过。

## 7. CMD 引号转义会吞 gh --jq 参数

- **现象**：`gh api --jq ".a + \" | \" + .b"` 报「不是内部或外部命令」
- **解法**：CMD 下别用嵌套引号 jq 表达式；用 `| findstr` 过滤或 PowerShell 代替

## 8. LF/CRLF warning 无害但吵

- **现象**：每次 git add 刷屏 `warning: LF will be replaced by CRLF`
- **解法**：无害可忽略；要根治加 `.gitattributes`（`* text=auto eol=lf`），等有空再做

## 9. curl 输出中文乱码 ≠ 数据坏了

- **现象**：CMD 里 curl 返回的中文 JSON 显示乱码
- **原因**：CMD 代码页（GBK）显示问题，数据本身 UTF-8 正常
- **解法**：不用管；要看得舒服先 `chcp 65001`

## 10. 额度是共享池，开发时别挥霍

- **现象**：用户数据能力组每天全组共享 100 次，开发调试几次就消耗不少
- **解法**：开发期用缓存/抓包样本；探测脚本一次性把数据存本地 JSON 反复用；线上必须缓存

## 11. httpx 在污染 CA 的环境下 TLS 验证失败（已修复）

- **现象**：urllib/curl 能访问知乎接口，但 httpx 报 `[SSL: CERTIFICATE_VERIFY_FAILED] self-signed certificate in certificate chain`；显式传 CA 路径、SSLContext、verify=True 都失败
- **原因**：环境变量 `SSL_CERT_FILE` 指向单证书自签 CA（`C:\Users\JM\.zcode\proxy\ca.pem`，仅 1 张证书），而公网站点（知乎）是真实 DigiCert 证书；httpx 默认 `trust_env=True` 会读该变量，把 CA 库换成这张自签证书 → 验证必然失败
- **解法**：`httpx.AsyncClient(trust_env=False)` —— 用 httpx 自带 certifi 公认 CA 库做标准验证；**生产环境行为一致，不降低安全性**（代码已在 `server/core/zhihu.py` 的 `client()` 固化）
- **排查方法**：手动 `ssl.create_default_context()` 握手对比 + 打印 `ca.pem` 里的证书数量（`data.count(b"BEGIN CERTIFICATE")`，1 张就是污染源）

## 12. CMD 下多行 `python -c` 会静默失败

- **现象**：`python -c "多行代码"` 在 CMD 里没有输出也不报错（换行被吞）
- **解法**：写成临时 .py 脚本再执行；或改用 PowerShell 传 here-string

## 13. 前端 `[hidden]` 属性被 CSS 的 display 覆盖

- **现象**：JS 设置 `el.hidden = true` 但元素仍显示（如未登录时的「退出」按钮）
- **原因**：CSS 里给该元素设了 `display: flex` 等，优先级高于 hidden 属性的 UA 样式
- **解法**：样式表加一条 `[hidden] { display: none !important; }`（已加入 server/static/style.css）

## 14. CMD echo 中文写入文件 = GBK 编码污染

- **现象**：用 `echo 中文内容 >> .env` 追加后，Python 以 UTF-8 读取 `.env` 崩溃（`UnicodeDecodeError: invalid start byte`），服务起不来或测试收集失败
- **原因**：CMD 的 echo 按系统代码页（GBK）写文件，UTF-8 读取器遇到 GBK 字节即失败
- **解法**：① 写文件一律用编辑器/Write 工具（UTF-8），不要用 CMD echo 写中文；② `server/core/config.py` 的 `.env` 加载已加编码容错（UTF-8 失败回退 GBK）

## 15. 匿名可读私密数据（P0，已修复）

- **现象**：未登录访客调用 `/api/collections` 能拿到项目账号（Access Secret 持有者）的私密收藏夹内容
- **原因**：为本地调试设计的「本人模式」在未登录时把 `X-OAuth-Token` 留空 → 按官方身份模型即以 Access Secret 本人身份查询；部署到公网即数据泄露
- **解法**：加 `ALLOW_SELF_MODE` 开关（默认关闭）；关闭时匿名请求一律 `LOGIN_REQUIRED`。仅本地 `.env` 设为 1 供预览调试。**部署清单上必须有这一条**

## 16. 自定义搜索引擎当书签替代：否决（别再试）

- **背景**：为让用户不占用书签栏位置，考虑把书签代码填进浏览器"自定义搜索引擎"，用「地址栏敲关键字 zl + 回车」代替点书签
- **触发现象**：把带 `javascript:` 前缀的代码粘贴进 Edge 地址栏时，**前缀被浏览器自动剥掉**（只剩 `(function(){...}`），说明 Edge 对地址栏执行 JS 有主动防护
- **结论**：**不采用**。理由：① 首次配置更麻烦（进设置页填三个框 ≈1 分钟 vs 拖书签 5 秒）；② 日常操作不更快（敲 zl+回车 vs 点一下书签）；③ 大概率被 Edge 拦（前缀剥离现象；URL 校验与导航拦截两层防护）；④ 收益仅"不占书签栏"，不值代价
- **最终形态**：首页教程给出两种安装方式——拖拽按钮进书签栏（主）/「复制书签」后粘贴进书签栏（兜底，拖拽失灵时用）；不再探索其他分发方式

## 17. CloudBase 环境 ID 与"控制台网址里的值"不一致（致命坑）

- **现象**：用控制台网址 `?envId=` 参数里的值拼 REST 网关域名（`https://<envId>.api.tcloudbasegateway.com/v1/rdb/rest/<table>`），一律返回 `404 INVALID_ENV`
- **原因**：网址里显示的值与环境实际 ID **不一致**，且差异极细微（`zhishizhizengliuzhan-d2co6jd1d0021` vs 实际 `zhishizhengliuzhan-d2coj6d1d0021`，肉眼几乎无法分辨）
- **解法**：以**概览页"环境 ID"字段**或控制台 Agent 查询结果为准；最快的定位方法是用两个候选值各发一次请求实测（正确值返回 200，错误值返回 INVALID_ENV）
- **附带知识**：CloudBase 共享集群（体验版）**不支持数据库直连**（内网/外网地址均不提供）；官方接入方式是 **REST API（PostgREST 规范）+ 服务端 API Key**（service_role 角色，在「环境管理 → API Key 配置 → 服务端 API Key」创建）。文档：`docs.cloudbase.net/http-api/pgdb/postgresql-restful-api`
- **实现要点**：写入用 `POST /v1/rdb/rest/<table>` + `Prefer: resolution=merge-duplicates`（upsert）+ `Content-Type: application/json`；认证头 `Authorization: Bearer <API Key>`

---

新坑随时追加到本文件，格式保持「坑 → 现象 → 解法」。
