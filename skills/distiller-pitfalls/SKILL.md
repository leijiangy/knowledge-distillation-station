---
name: distiller-pitfalls
description: 知识蒸馏站开发过程中踩过的坑与解法（Windows 环境 + ZCode 沙箱 + 知乎官方工具链）。遇到 git 推送失败、网络不通、官方脚本报错、飞书文档读不了、凭证配置异常等类似问题时先查这里；仅适用于本机本项目的环境。
---

# 踩坑记录（每条 = 坑 → 现象 → 解法）

## 1. 官方黑客松脚本是 macOS 专属

- **现象**：`init_project.mjs` 报 `Unable to inspect bundled official Skill archive`；`set_app_key.mjs` 直接报 `supports secure app_key storage on macOS only`
- **原因**：脚本硬编码 `/usr/bin/unzip`、`/bin/bash`、`/usr/bin/security`（macOS 钥匙串）
- **解法**：Windows 用 `skills/zhihu-v2026s2/win_init.mjs` + `win_finalize.mjs` 等价初始化（含同样的 SHA-256 校验）；app_key 不进钥匙串，走 `.env` 环境变量——官方 `lib/oauth.mjs` 原生优先读环境变量（第 92-93 行）

## 2. ZCode 沙箱网络：只有 gh.exe 的 API 流量能出网

- **现象**：`git push/fetch` 直连 GitHub 超时或 Connection reset；`curl` 直连外网超时；但 `gh api` 一切正常
- **原因**：沙箱按进程放行网络，git.exe/curl.exe 被拦
- **解法**：GitHub 同步用 `gh api`（contents API 传文件 / git data API 构造提交）；或让用户在自己终端跑 git（他的终端没有此限制）

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

---

新坑随时追加到本文件，格式保持「坑 → 现象 → 解法」。
