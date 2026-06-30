# Agent Mail · GitHub Actions 版

零 VPS 的轻量邮件自动化。两件事：

1. **每天 09:00（CST）发送** `daily.hiwd.com` 的当日简报给订阅者。
2. **守住你的私人邮箱**：除非主题以 `[AGENT]` / `[SEND_EMAIL]` / `[DAILY]` / `[UNSUBSCRIBE]` 开头，否则邮件**完全不被读取、不被标记、不被移动**。

---

## 设计要点（必读）

* **内容源**：`docs/rss.xml` → fallback 到 `docs/archive/YYYY-MM/YYYY-MM-DD.html`。两者都没有就**不发**，避免发空邮件。
* **发送**：默认走 **QQ 邮箱 SMTP** —— 文档化、CI 友好、可单测。`agent.qq.com` CLI 在 `agent_mail/send.py:_send_agently()` 留了占位与两阶段确认骨架，待官方文档明确 send 命令后替换两行即可。
* **个人邮件防误发**：`[SEND_EMAIL]` 任务**默认仅写草稿**到 `agent_mail/drafts/`，不发出去。即使主题里写了 `CONFIRM_SEND`，也只在 Secret `AUTO_SEND_PERSONAL=1` 时才真正发送。
* **`drafts/*.eml` 不进 git**（已在 `.gitignore`），避免私人正文上链。
* **3 天未读转发**：只转发主题不转发正文。`PERSONAL_EMAIL` 未配置则功能关闭。
* **state.json**：`last_digest_date`、`processed_message_ids`、`unread_first_seen`、`forwarded_unread_ids` 等都在这一个文件里。每次 workflow 跑完 commit 回 main —— 这是状态持久化的唯一手段。

---

## 仓库结构

```
agent_mail/
  __init__.py
  config.py            # 读环境变量
  state.py             # state.json 读写
  subscribers.py       # 订阅者列表 IO
  content.py           # RSS / archive 取当日内容
  render.py            # 邮件 HTML/text 模板
  send.py              # SMTP 主路径 + agently CLI 占位
  inbox.py             # IMAP 任务路由 + 3 天未读摘要
  subscribers.json     # ← 编辑这个文件管理订阅者
  state/state.json     # workflow 写回，请勿手改
  drafts/.gitkeep      # *.eml 不进 git
scripts/
  digest.py            # 09:00 调用
  inbox_run.py         # 每 30 分钟调用
.github/workflows/
  agent-mail-digest.yml
  agent-mail-inbox.yml
```

---

## 配置 GitHub Secrets

进入 **Settings → Secrets and variables → Actions**，添加：

### 必填（发送 digest）

| Secret | 值 | 说明 |
|---|---|---|
| `SMTP_HOST` | `smtp.qq.com` | QQ 邮箱 SMTP |
| `SMTP_PORT` | `465` | SSL |
| `SMTP_USER` | `iworld@agent.qq.com` | 登录用户名 |
| `SMTP_PASS` | *授权码* | **不是登录密码**，是 QQ 邮箱设置里生成的"授权码" |
| `SMTP_FROM` | `iworld@agent.qq.com` | 通常同 `SMTP_USER` |
| `SMTP_FROM_NAME` | `hiwd daily` | 显示名 |

### 选填（开启 inbox 任务路由）

| Secret | 值 | 说明 |
|---|---|---|
| `IMAP_HOST` | `imap.qq.com` | |
| `IMAP_PORT` | `993` | |
| `IMAP_USER` | 同 `SMTP_USER` 即可 | |
| `IMAP_PASS` | 同 `SMTP_PASS` 即可 | |
| `PERSONAL_EMAIL` | `you@gmail.com` | 3 天未读摘要的转发目标，不填则不转发 |
| `AUTO_SEND_PERSONAL` | `0` | 默认 `0`（仅草稿）。设 `1` 才允许 `[SEND_EMAIL]+CONFIRM_SEND` 自动发出 |
| `FORWARD_IDLE_DAYS` | `3` | 调整闲置天数 |
| `SENDER_BACKEND` | `smtp` | 不要设成 `agently`，除非你已经填好了 `_send_agently()` |

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
* 用户回复一封主题以 `[UNSUBSCRIBE]` 开头的邮件，inbox poller 会自动从列表移除（仅当 IMAP 已配置）。

订阅入口：在 `https://daily.hiwd.com/rss.xml` 的页面附近放一个 `mailto:iworld@agent.qq.com?subject=[AGENT]%20subscribe` 链接，或单独做个静态表单都行。

---

## 测试

### 1. 本地 dry-run（不发邮件，生成 .eml 文件）

```bash
# 设最少必要变量
export SMTP_HOST=smtp.qq.com SMTP_PORT=465 \
       SMTP_USER=iworld@agent.qq.com SMTP_PASS=xxx \
       SMTP_FROM=iworld@agent.qq.com SMTP_FROM_NAME='hiwd daily'

# 把 subscribers.json 里改成你自己的邮箱测试
python scripts/digest.py --dry-run --force --limit 1
ls agent_mail/drafts/    # 查看生成的 .eml，可在 Apple Mail / Thunderbird 里打开
```

### 2. CI dry-run（GitHub UI 手动触发）

* 打开 **Actions → Agent Mail · Daily Digest → Run workflow**
* `dry_run = true`，跑完到 **Artifacts** 下载 `drafts-<run-id>` 包
* 满意之后再 `dry_run = false` 真发，或等明早 09:00 自动执行

### 3. 测试 inbox 路由

给 `iworld@agent.qq.com` 发一封：

* 主题 `[SEND_EMAIL] to:test@example.com hello world` → 应该进 `drafts/`，不会发出
* 主题 `[SEND_EMAIL] to:test@example.com CONFIRM_SEND hello` + Secret `AUTO_SEND_PERSONAL=1` → 真正发出
* 主题 `[UNSUBSCRIBE]` 来自订阅者 → 自动移除 + state.json commit

---

## 手动触发

* **重发某天**：`workflow_dispatch` → 填 `date=2026-06-29` + `force=true`
* **暂停**：把对应 workflow 文件改名为 `*.yml.disabled`，或在 Actions 设置里 disable
* **跳过转发**：手动触发 inbox workflow 时 `no_forward=true`

---

## 把 SMTP 替换成 agent.qq.com CLI

`agent_mail/send.py:_send_agently()` 已经写好骨架，包含两阶段确认（先调一次拿 `confirmation_token`，第二次带 `--confirmation-token` 确认）。落地步骤：

1. 在本机跑一次 `agently-cli auth login`，确认能发邮件
2. 找到真实的发送命令语法（例如 `agently-cli send-mail --to ... --subject ... --body-file ...`）和 token 字段名（`confirmation_token` 还是 `ctk`），把 `_send_agently()` 顶部的 `raise NotImplementedError` 删掉、把 `cmd1` / `cmd2` 改成真实 flag
3. 解决 CI 鉴权 —— 通常需要把本地登录后的 token 文件（如 `~/.config/agently/credentials.json`）作为 base64 Secret 注入，workflow 里恢复到 runner 同样路径
4. 设 Secret `SENDER_BACKEND=agently`

在 step 3 没解决之前**不要**把 `SENDER_BACKEND` 切到 `agently` —— OAuth 交互式登录在 GitHub Actions 的 headless 环境里会卡死。

---

## 安全保证（默认值复述一遍）

* 私人邮件：**永远不读 body，永远不 mark seen**（IMAP 用 `BODY.PEEK`）
* `[SEND_EMAIL]` 任务：默认仅草稿
* 3 天未读转发：默认只发主题
* 草稿不进 git
* 所有凭证在 Secrets，代码里没有任何硬编码
