/**
 * mail.hiwd.com — subscribe API for hiwd daily.
 *
 * Routes:
 *   POST /subscribe   { email }          → commits subscribers.json on GitHub
 *   POST /unsubscribe { email, token? }  → marks paused: true (or removes if token)
 *   GET  /confirm?token=...              → flips paused: false (double opt-in finish)
 *   GET  /healthz                        → "ok"
 *
 * Design notes:
 *   - The Worker NEVER stores email anywhere except by committing to the repo.
 *   - Double opt-in: a new email lands as paused=true with a confirm_token; a
 *     confirmation email is sent via Resend (or any SMTP relay). Only after
 *     the user clicks /confirm does paused flip to false.
 *   - We use the GitHub Contents API (PUT /repos/.../contents/...). It needs
 *     the file's current `sha` so we GET it first; the API rejects the PUT
 *     on stale sha → we retry once with fresh sha to absorb concurrent writes.
 *   - Idempotent: posting an existing email returns the existing status,
 *     never creates a duplicate row.
 *
 * Secrets (set via `wrangler secret put`):
 *   GITHUB_TOKEN     — fine-grained PAT with Contents: write on this repo
 *   RESEND_API_KEY   — optional; if unset, double opt-in is disabled and the
 *                      address is added directly (paused=false). Useful for
 *                      first-bring-up before Resend is configured.
 *   CONFIRM_SECRET   — random string used to sign confirm tokens (HMAC)
 *
 * Vars (in wrangler.toml):
 *   GITHUB_REPO      — "ilivecom/daily"
 *   GITHUB_BRANCH    — "main"
 *   FILE_PATH        — "agent_mail/subscribers.json"
 *   FROM_EMAIL       — "iworld@agent.qq.com"  (only for confirm mail sender)
 *   FROM_NAME        — "hiwd daily"
 *   SITE_BASE        — "https://daily.hiwd.com"
 */

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "https://daily.hiwd.com",
  "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
  "Access-Control-Max-Age": "86400",
};

const json = (status, body) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", ...CORS_HEADERS },
  });

const text = (status, body, extra = {}) =>
  new Response(body, {
    status,
    headers: { "Content-Type": "text/html; charset=utf-8", ...CORS_HEADERS, ...extra },
  });

const EMAIL_RE = /^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$/;

export default {
  async fetch(req, env) {
    const url = new URL(req.url);

    if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS_HEADERS });
    if (url.pathname === "/healthz") return text(200, "ok");

    try {
      if (req.method === "POST" && url.pathname === "/subscribe") return await handleSubscribe(req, env);
      if (req.method === "POST" && url.pathname === "/unsubscribe") return await handleUnsubscribe(req, env);
      if (req.method === "GET" && url.pathname === "/confirm") return await handleConfirm(url, env);
    } catch (e) {
      console.log("error:", e?.stack || String(e));
      return json(500, { ok: false, error: "internal_error" });
    }

    return json(404, { ok: false, error: "not_found" });
  },
};

// ─── handlers ───────────────────────────────────────────────────────────────

async function handleSubscribe(req, env) {
  const body = await safeJson(req);
  const email = String(body?.email || "").trim().toLowerCase();
  if (!EMAIL_RE.test(email)) return json(400, { ok: false, error: "invalid_email" });

  const { content, sha } = await getFile(env);
  let list = JSON.parse(content);
  if (!Array.isArray(list)) list = [];

  const existing = list.find((s) => (s.email || "").toLowerCase() === email);
  const useDoubleOptIn = !!env.RESEND_API_KEY;

  if (existing) {
    if (!existing.paused) return json(200, { ok: true, status: "already_subscribed" });
    // Re-subscribe a paused address — same opt-in flow.
    if (useDoubleOptIn) {
      const token = await signToken(email, env.CONFIRM_SECRET);
      await sendConfirmEmail(email, token, env);
      return json(200, { ok: true, status: "confirmation_sent" });
    } else {
      existing.paused = false;
      existing.resubscribed_at = today();
      await putFile(env, list, sha, `chore(mail): resubscribe ${email}`);
      return json(200, { ok: true, status: "resubscribed" });
    }
  }

  const entry = {
    email,
    name: email.split("@")[0],
    subscribed_at: today(),
  };

  if (useDoubleOptIn) {
    entry.paused = true; // becomes false on /confirm
    list.push(entry);
    await putFile(env, list, sha, `chore(mail): pending subscribe ${email}`);
    const token = await signToken(email, env.CONFIRM_SECRET);
    await sendConfirmEmail(email, token, env);
    return json(200, { ok: true, status: "confirmation_sent" });
  } else {
    list.push(entry);
    await putFile(env, list, sha, `chore(mail): subscribe ${email}`);
    return json(200, { ok: true, status: "subscribed" });
  }
}

async function handleUnsubscribe(req, env) {
  const body = await safeJson(req);
  const email = String(body?.email || "").trim().toLowerCase();
  if (!EMAIL_RE.test(email)) return json(400, { ok: false, error: "invalid_email" });

  const { content, sha } = await getFile(env);
  let list = JSON.parse(content);
  const before = list.length;
  list = list.filter((s) => (s.email || "").toLowerCase() !== email);
  if (list.length === before) return json(200, { ok: true, status: "not_subscribed" });
  await putFile(env, list, sha, `chore(mail): unsubscribe ${email}`);
  return json(200, { ok: true, status: "unsubscribed" });
}

async function handleConfirm(url, env) {
  const token = url.searchParams.get("token") || "";
  const email = await verifyToken(token, env.CONFIRM_SECRET);
  if (!email) return text(400, confirmPage("链接已失效或被篡改", false, env));

  const { content, sha } = await getFile(env);
  let list = JSON.parse(content);
  const entry = list.find((s) => (s.email || "").toLowerCase() === email);
  if (!entry) return text(404, confirmPage("未找到该订阅记录，可能已被取消", false, env));
  if (!entry.paused) return text(200, confirmPage("已订阅，无需重复确认", true, env));

  entry.paused = false;
  entry.confirmed_at = today();
  await putFile(env, list, sha, `chore(mail): confirm ${email}`);
  return text(200, confirmPage("订阅已确认，明早收第一封简报", true, env));
}

// ─── GitHub Contents API ────────────────────────────────────────────────────

async function ghHeaders(env) {
  return {
    "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "hiwd-mail-worker",
  };
}

async function getFile(env) {
  const u = `https://api.github.com/repos/${env.GITHUB_REPO}/contents/${env.FILE_PATH}?ref=${env.GITHUB_BRANCH}`;
  const r = await fetch(u, { headers: await ghHeaders(env) });
  if (!r.ok) throw new Error(`gh GET ${r.status}: ${await r.text()}`);
  const j = await r.json();
  // GitHub returns base64 with newlines; decode safely
  const content = atob(j.content.replace(/\n/g, ""));
  return { content: utf8(content), sha: j.sha };
}

async function putFile(env, list, sha, message, retry = true) {
  const u = `https://api.github.com/repos/${env.GITHUB_REPO}/contents/${env.FILE_PATH}`;
  const next = JSON.stringify(list, null, 2) + "\n";
  const body = {
    message,
    branch: env.GITHUB_BRANCH,
    content: btoa(unescape(encodeURIComponent(next))),
    sha,
    committer: { name: "Agent Mail Bot", email: "41898282+github-actions[bot]@users.noreply.github.com" },
  };
  const r = await fetch(u, {
    method: "PUT",
    headers: { ...(await ghHeaders(env)), "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (r.status === 409 && retry) {
    // Stale sha — refetch and retry once.
    const fresh = await getFile(env);
    return putFile(env, list, fresh.sha, message, false);
  }
  if (!r.ok) throw new Error(`gh PUT ${r.status}: ${await r.text()}`);
  return r.json();
}

// ─── confirmation email via Resend ──────────────────────────────────────────

async function sendConfirmEmail(email, token, env) {
  if (!env.RESEND_API_KEY) return;
  const link = `https://mail.hiwd.com/confirm?token=${encodeURIComponent(token)}`;
  const html = `
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'PingFang SC', sans-serif; max-width: 560px; margin: 0 auto; padding: 24px; color: #1d1d1f; line-height: 1.7;">
      <p>嗨，</p>
      <p>请点击下面的链接，确认订阅 <strong>hiwd daily</strong> 每日 AI 简报：</p>
      <p><a href="${link}" style="display:inline-block;padding:10px 18px;background:#00C2B3;color:#fff;text-decoration:none;border-radius:8px;">确认订阅</a></p>
      <p style="font-size:13px;color:#86868b;">如果按钮无法点击，请复制此链接到浏览器：<br/><a href="${link}">${link}</a></p>
      <p style="font-size:13px;color:#86868b;">若你未发起过订阅，忽略此邮件即可。</p>
    </div>
  `;
  const r = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${env.RESEND_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      from: `${env.FROM_NAME} <${env.FROM_EMAIL}>`,
      to: [email],
      subject: "确认订阅 hiwd daily",
      html,
    }),
  });
  if (!r.ok) {
    console.log("resend failed:", r.status, await r.text());
    // Do not surface to user; their entry is already committed as paused=true.
  }
}

// ─── token signing (HMAC-SHA256, base64url) ─────────────────────────────────

async function signToken(email, secret) {
  const ts = Math.floor(Date.now() / 1000);
  const payload = `${email}|${ts}`;
  const sig = await hmac(payload, secret);
  return b64url(payload) + "." + sig;
}

async function verifyToken(token, secret) {
  if (!token || !token.includes(".")) return null;
  const [p, sig] = token.split(".");
  let payload;
  try { payload = b64urlDecode(p); } catch { return null; }
  const expect = await hmac(payload, secret);
  if (sig !== expect) return null;
  const [email, tsStr] = payload.split("|");
  const ts = parseInt(tsStr, 10);
  if (!Number.isFinite(ts)) return null;
  // 7-day validity window
  if (Date.now() / 1000 - ts > 7 * 24 * 3600) return null;
  return email;
}

async function hmac(payload, secret) {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const sigBuf = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(payload));
  return b64urlBytes(new Uint8Array(sigBuf));
}

// ─── helpers ────────────────────────────────────────────────────────────────

const today = () => new Date().toISOString().slice(0, 10);

async function safeJson(req) {
  try { return await req.json(); } catch { return {}; }
}

function utf8(binary) {
  return decodeURIComponent(escape(binary));
}

function b64url(s) {
  return btoa(unescape(encodeURIComponent(s))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function b64urlBytes(bytes) {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function b64urlDecode(s) {
  const pad = s.length % 4 ? "=".repeat(4 - (s.length % 4)) : "";
  return decodeURIComponent(escape(atob((s + pad).replace(/-/g, "+").replace(/_/g, "/"))));
}

function confirmPage(message, ok, env) {
  const accent = ok ? "#00C2B3" : "#d4380d";
  return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>${ok ? "订阅确认" : "确认失败"} · hiwd daily</title>
  <style>
    body { margin:0; font-family: -apple-system, BlinkMacSystemFont, 'PingFang SC', sans-serif; background:#f5f5f7; color:#1d1d1f; }
    main { max-width: 520px; margin: 80px auto; padding: 36px 32px; background:#fff; border-radius: 16px; box-shadow: 0 8px 24px rgba(15,23,42,.06); text-align:center; }
    h1 { font-size: 22px; margin: 0 0 12px; padding-left:14px; position:relative; display:inline-block; }
    h1::before { content:""; position:absolute; left:0; top:50%; transform:translateY(-50%); width:3px; height:20px; background:${accent}; border-radius:2px; }
    p { color:#555; line-height:1.7; }
    a.back { display:inline-block; margin-top:16px; padding:10px 20px; background:${accent}; color:#fff; text-decoration:none; border-radius:8px; font-size:15px; }
    a.back:hover { opacity:0.85; }
  </style></head>
  <body><main>
    <h1>${ok ? "✓ 已确认" : "× 确认失败"}</h1>
    <p>${message}</p>
    <a class="back" href="${env.SITE_BASE}/">返回 daily.hiwd.com</a>
  </main></body></html>`;
}
