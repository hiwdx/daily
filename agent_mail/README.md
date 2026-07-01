# Agent Mail · GitHub Actions 版

零 VPS、零 Mac、纯云的日报邮件自动化。

**每天 09:00（CST）** 从 `docs/rss.xml` 或 `docs/archive/*.html` 取当日 `daily.hiwd.com` 简报，通过 **`agent.qq.com` (agently-cli) HTTP API** 发送给 `agent_mail/subscribers.json` 里的订阅者。

---

## 关键设计

* **发送后端**：`agently-cli message +send`（Tencent Agent Mail），两阶段确认（先拿 `confirmation_token`、再确认发出，全部脚本自动完成）。**不用 SMTP、不用 IMAP、不用邮件服务器**。
* **CI 认证**：`bootstrap_token.enc` + keychain `master.key` 从本机一次性导出到 GitHub Secrets；每次 workflow 起 `dbus + gnome-keyring` 还原 keychain → `agently-cli auth refresh` 拿短期 access token → 发送。
* **内容源**：`docs/rss.xml` → fallback `docs/archive/YYYY-MM/YYYY-MM-DD.html`；两者都没有则**不发**（避免发空邮件）。
* **幂等**：`state.last_digest_date` 记录当日已发；同一天重跑跳过（`--force` 覆盖）。三档 cron（08:35 / 09:10 / 09:40 CST）确保 GitHub 高峰期延迟不影响送达。
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
| `AGENTLY_BOOTSTRAP_ENC_B64` | `base64 -i "$HOME/Library/Application Support/agently-cli/bootstrap_token.enc"` | 加密的长期 refresh token |
| `AGENTLY_MASTER_KEY` | `security find-generic-password -s agently-cli -a master.key -w` | 解密 refresh token 的对称密钥（保留 `go-keyring-base64:` 前缀） |

⚠️ **导出前提**：本机已跑过 `agently-cli auth login` 完成扫码授权。

⚠️ **敏感度**：这两个 Secret 加起来 = 你的 agent.qq.com 账号完全控制权。除了 GitHub Secrets 外不要留副本、不要贴到聊天/云笔记里。

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
DRY_RUN=1 python scripts/digest.py --force --limit 1
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

`bootstrap_token.enc` 里存的是**长期** refresh token（月级别有效），access token 由 CI 每次自己 `auth refresh` 现取现用（1 小时过期，不用管）。

如果 CI 跑到 `auth refresh` 步骤开始报 `expired_token` / `invalid_grant`：

1. 本机 `agently-cli auth login` 重新扫码
2. 重新导出 `AGENTLY_BOOTSTRAP_ENC_B64` 覆盖 GitHub Secret
3. 如果本机 keychain 有变（重装/重置过），也重新导出 `AGENTLY_MASTER_KEY`

---

## 调试提示

* workflow 的 `Refresh access token + send digest` 步骤跑了 `agently-cli auth status` 和 `+me`，日志能看到 token 有效期和账号邮箱
* 如果 `secret-tool store` 不报错但 `auth refresh` 依然说未登录，检查 `AGENTLY_MASTER_KEY` 是否忘了 `go-keyring-base64:` 前缀
* CLI 日志加 `AGENTLY_CLI_DEBUG_HTTP=1` 会打印所有 HTTP 请求
* Beta 限额（截至 2026-07）：**50 封/天**、**1 MB/封**、**1 GB/账号**
