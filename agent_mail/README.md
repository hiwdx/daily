# Agent Mail · GitHub Actions 版

零 VPS、零 Mac、纯云的日报邮件自动化。

**每天 09:00（CST）** 从 `docs/rss.xml` 或 `docs/archive/*.html` 取当日 `daily.hiwd.com` 简报，通过 **`agent.qq.com` (agently-cli) HTTP API** 发送给 `agent_mail/subscribers.json` 里的订阅者。

---

## 关键设计

* **发送后端**：`agently-cli message +send`（Tencent Agent Mail），两阶段确认（先拿 `confirmation_token`、再确认发出，全部脚本自动完成）。**不用 SMTP、不用 IMAP、不用邮件服务器**。
* **CI 认证**：先跑一次 `Agent Mail · One-time Linux Login`，在 Linux runner 上完成 `agently-cli auth login`，把生成的完整配置包保存为 GitHub Secret `AGENTLY_CONFIG_TAR_B64`；每天 workflow 只恢复这个包并调用 CLI。
* **内容源**：`docs/rss.xml` → fallback `docs/archive/YYYY-MM/YYYY-MM-DD.html`；两者都没有则**不发**（避免发空邮件）。
* **幂等**：`state.last_digest_date` 记录当日已完整发送；同一天重跑跳过（`--force` 覆盖）。三档 cron（09:00 / 09:15 / 09:30 CST）配合按收件人记录，失败重跑时不会误跳过未发送用户。
* **`drafts/*.eml` 不进 git**（`.gitignore`）。dry-run 生成的草稿由 `actions/upload-artifact` 收集。

---

## 仓库结构

```
agent_mail/
  __init__.py
  config.py            # 读 env → RuntimeConf(agently, dry_run)
  state.py             # state.json 读写
  subscribers.py       # 订阅者列表 IO
  content.py           # RSS / archive 取当日内容
  render.py            # 邮件 HTML/text 模板
  send.py              # agently-cli 两阶段发送 + drafts 写 .eml
  subscribers.json     # ← 编辑这个文件管理订阅者
  state/state.json     # workflow 写回，请勿手改
  drafts/.gitkeep      # *.eml 不进 git
scripts/
  digest.py            # 每日 09:00 调用
.github/workflows/
  agent-mail-digest.yml
```

---

## 一次性配置：GitHub Secrets

进入 **Settings → Secrets and variables → Actions**，添加：

| Secret | 从哪来 | 说明 |
|---|---|---|
| `AGENTLY_CONFIG_TAR_B64` | Actions → `Agent Mail · One-time Linux Login` 跑完后日志里的 `AGENTLY_CONFIG_TAR_B64` | Linux 版 agently-cli 的完整授权配置包 |

步骤：

1. 打开 **Actions → Agent Mail · One-time Linux Login → Run workflow**。
2. 在日志中找到 `https://agent.qq.com/...` 授权链接，按提示完成 `iworld@agent.qq.com` 授权。
3. 复制日志里 `AGENTLY_CONFIG_TAR_B64_START/END` 之间的一整行，保存到 **Settings → Secrets and variables → Actions → New repository secret**，名称为 `AGENTLY_CONFIG_TAR_B64`。
4. 手动触发 **Agent Mail · Daily Digest**，先用 `dry_run=true` 检查草稿，再用 `dry_run=false force=true limit=1` 试发。

⚠️ **敏感度**：`AGENTLY_CONFIG_TAR_B64` = 你的 agent.qq.com 账号发信权限。除了 GitHub Secrets 外不要留副本、不要贴到聊天/云笔记里。

---

## 维护订阅者

直接编辑 `agent_mail/subscribers.json`：

```json
[
  {"email": "alice@example.com", "name": "Alice", "subscribed_at": "2026-06-30"},
  {"email": "bob@example.com", "name": "Bob", "paused": true}
]
```

* `paused: true` → 跳过但不删除。
* 从网站前端订阅：Cloudflare Worker 收到订阅请求 → PR 到本仓库 `subscribers.json`。
* 从网站前端退订：`https://mail.hiwd.com/unsubscribe`。

---

## 测试

### 本地 dry-run（不发邮件，生成 .eml）

```bash
DRY_RUN=1 python3 scripts/digest.py --force --limit 1
ls agent_mail/drafts/
```

.eml 文件可用 Apple Mail / Thunderbird 打开预览。dry-run 不需要 agently 凭证。

### CI dry-run（GitHub UI 手动触发）

* **Actions → Agent Mail · Daily Digest → Run workflow**
* `dry_run = true`
* 跑完到 **Artifacts** 下载 `drafts-<run-id>` 检查内容

### CI 真发（手动触发）

* `dry_run = false`, `force = true`（可选，忽略当日幂等）
* `date = 2026-06-29`（可选，重发某一天）

---

## 手动触发常见场景

* **重发某一天**：`workflow_dispatch` → `date=2026-06-29 force=true`
* **补发今天**：`workflow_dispatch` → 三档 cron 都错过了 → `force=true`
* **暂停自动发**：把 `agent-mail-digest.yml` 改名 `.disabled`，或在 Actions 页 disable

---

## 凭证维护

`AGENTLY_CONFIG_TAR_B64` 里存的是 Linux runner 上 `agently-cli auth login` 生成的授权配置。daily workflow 会先 `agently-cli auth refresh`，再校验 `+me` 返回的邮箱包含 `iworld@agent.qq.com`，最后发信。

如果 CI 跑到 `Verify agently account` 步骤开始报 `expired_token` / `invalid_grant`：

1. 重新跑 **Agent Mail · One-time Linux Login**
2. 用新的日志输出覆盖 GitHub Secret `AGENTLY_CONFIG_TAR_B64`

---

## 调试提示

* workflow 的 `Verify agently account` 步骤会跑 `agently-cli auth status` 和 `+me`，日志能看到授权状态；如果不是 `iworld@agent.qq.com` 会直接失败
* CLI 日志加 `AGENTLY_CLI_DEBUG_HTTP=1` 会打印所有 HTTP 请求
* Beta 限额（截至 2026-07）：**50 封/天**、**1 MB/封**、**1 GB/账号**
