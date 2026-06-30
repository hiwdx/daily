# mail.hiwd.com — Cloudflare Worker 部署指南

这个 Worker 接收 `daily.hiwd.com/rss.xml` 上"邮箱订阅"按钮的 POST 请求，
直接 commit 到 `agent_mail/subscribers.json`。

**daily.hiwd.com 的 DNS 不变**（仍灰云直连 GitHub Pages，国内访问速度不变）。
仅新增 `mail.hiwd.com` 子域走 Cloudflare 代理 —— 用户只在点击订阅按钮的那一次访问命中。

---

## 你需要做的 5 步（每步 ≤ 2 分钟）

### 1. 安装 wrangler（一次性）

```bash
cd /Users/andrew/Documents/claudecode/cf-worker
npm install   # 装 wrangler
npx wrangler login   # 浏览器弹出 Cloudflare 授权页面，点 Allow
```

### 2. 生成 GitHub Personal Access Token

1. 打开 <https://github.com/settings/tokens?type=beta>
2. **Generate new token** → "Fine-grained personal access token"
3. **Token name**：`hiwd-mail-worker`
4. **Expiration**：90 天（到期后重新生成；或选 No expiration 自担风险）
5. **Repository access** → Only select repositories → 勾 `ilivecom/daily`
6. **Repository permissions**：
   - **Contents** → **Read and write**
   - 其它全部 No access
7. 点 Generate → **复制 token**（这是你唯一一次能看到完整 token 的机会）

### 3. 在 cf-worker 目录里设置 3 个 Secret

```bash
cd /Users/andrew/Documents/claudecode/cf-worker

# 粘贴上一步复制的 GitHub PAT
npx wrangler secret put GITHUB_TOKEN

# 任意 32 位以上随机字符串，用于签名确认链接 token
# 推荐用：openssl rand -hex 32
npx wrangler secret put CONFIRM_SECRET

# （可选）确认邮件用 Resend 发送，跳过这步则订阅是单步式（无 double opt-in）
# 如要启用：去 https://resend.com 注册 → API Keys 生成一个 → 这里粘贴
npx wrangler secret put RESEND_API_KEY
```

每条命令运行后会提示输入 secret，粘贴 → 回车即可。Secret 存在 Cloudflare 加密保险库，**不进 git**。

### 4. 部署

```bash
cd /Users/andrew/Documents/claudecode/cf-worker
npx wrangler deploy
```

成功输出大概是：

```
Uploaded hiwd-mail (1.23 sec)
Published hiwd-mail
  mail.hiwd.com/* (custom domain)
```

wrangler 会自动在 Cloudflare 上：
- 创建 Worker
- 创建 `mail.hiwd.com` 的 CNAME（橙云代理）
- 把 `mail.hiwd.com/*` 路由到 Worker

### 5. 测试

```bash
# 健康检查
curl https://mail.hiwd.com/healthz
# 应该返回: ok

# 订阅一个测试邮箱（用你自己的）
curl -X POST https://mail.hiwd.com/subscribe \
  -H 'Content-Type: application/json' \
  -d '{"email":"YOUR@EMAIL.COM"}'
# 成功返回: {"ok":true,"status":"confirmation_sent"} 或 "subscribed"
```

打开 `https://daily.hiwd.com/rss.xml`（强刷一次清缓存），在邮箱订阅框输入邮箱 → 应该看到一秒内出现"已发送确认邮件"提示。

---

## 验证 GitHub commit 已写入

```bash
git fetch origin main
git log origin/main --oneline -5 | grep "subscribe\|mail"
```

应该能看到形如 `chore(mail): pending subscribe foo@bar.com` 的 bot commit。

---

## 工作模式说明

### 启用 Double Opt-In（推荐，需要 RESEND_API_KEY）

订阅流程：

```
用户输入邮箱 → 点订阅
    ↓
Worker 把 paused: true 的 entry commit 进 subscribers.json
    ↓
Worker 通过 Resend 发一封"确认订阅"邮件，带 HMAC 签名链接
    ↓
用户点击邮件链接 → mail.hiwd.com/confirm?token=...
    ↓
Worker 验证 token → 把 paused: false → commit
    ↓
下一次 daily digest workflow 跑时该邮箱开始收到简报
```

防止有人恶意提交别人邮箱（因为没确认前 paused=true，digest 不会发给他）。

### 单步订阅（不设 RESEND_API_KEY）

省掉确认邮件那一步，提交即生效。**缺点**：任何人能往订阅表里塞任意邮箱（虽然 commit 历史可追溯，但用户会收到莫名其妙的简报）。**仅推荐用于初期验证**。

---

## 如果想退到 mailto 模式

最简单：在 Cloudflare dashboard 把 `mail.hiwd.com` 这条 DNS 记录删掉，
或在 cf-worker 目录跑 `npx wrangler delete`。
然后把 `docs/rss.xsl` 里的 fetch JS 还原成之前的 mailto: 版本（`git log docs/rss.xsl` 找 commit `f504e51` 或更早的版本）。

---

## 故障排查

| 现象 | 检查 |
|---|---|
| `curl mail.hiwd.com/healthz` 502/525 | DNS 还没生效，等几分钟；或 wrangler deploy 没成功 |
| 订阅返回 `internal_error` | `npx wrangler tail` 看实时日志，多半是 GITHUB_TOKEN 权限不对 |
| GitHub commit 没出现 | PAT scope 不够，重新生成时确认 Contents: Read and write |
| 浏览器报 CORS | `worker.js` 的 `CORS_HEADERS.Access-Control-Allow-Origin` 必须是 `https://daily.hiwd.com`（已设好） |
| 确认邮件没收到 | RESEND_API_KEY 没配 / Resend 没验证发件域名 / 进了垃圾箱 |
