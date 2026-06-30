<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet version="1.0"
  xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
  xmlns:atom="http://www.w3.org/2005/Atom">
  <xsl:output method="html" encoding="UTF-8" indent="yes"
    doctype-system="about:legacy-compat" />

  <xsl:template match="/rss/channel">
    <html lang="zh-CN">
    <head>
      <meta charset="UTF-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1.0" />
      <title><xsl:value-of select="title" /> · 订阅</title>
      <link rel="icon" type="image/x-icon" href="/favicon.ico?v=3" />
      <link rel="preconnect" href="https://fonts.googleapis.com" />
      <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin="" />
      <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400&amp;display=swap" rel="stylesheet" />
      <style>
        *, *::before, *::after { box-sizing: border-box; }
        body {
          margin: 0; padding: 0;
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
          font-size: 17px; line-height: 1.82;
          background-color: #f5f5f7; color: #1d1d1f;
          -webkit-text-size-adjust: 100%; text-size-adjust: 100%;
          overflow-wrap: break-word; word-break: break-word;
        }

        /* Logo (matches daily.hiwd.com) */
        #logo {
          position: fixed; top: 30px; left: 20px; z-index: 9999;
          display: flex; align-items: center; gap: 12px;
          text-decoration: none; outline: none; border: none;
          -webkit-tap-highlight-color: transparent;
          white-space: nowrap; user-select: none;
        }
        #logo-hiwd, #logo-sub {
          font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
          font-size: 26px; font-weight: 400;
          letter-spacing: -0.02em; color: #1d1d1f; line-height: 1;
        }
        #logo-i { position: relative; display: inline-block; }
        #logo-i::after {
          content: ''; position: absolute;
          top: .06em; left: 50%; transform: translateX(-50%);
          width: .155em; height: .155em;
          border-radius: 50%; background: #00C2B3;
        }
        #logo-rule {
          width: 1px; height: 17px;
          background: rgba(29,29,31,.18); flex-shrink: 0;
        }

        #content {
          margin-top: 84px; margin-left: auto; margin-right: auto;
          width: min(90%, 840px);
          padding: 40px 42px 34px;
          background-color: rgba(255, 255, 255, 0.94);
          border: 1px solid rgba(15, 23, 42, 0.05);
          box-shadow: 0 14px 40px rgba(15, 23, 42, 0.06);
          border-radius: 24px; margin-bottom: 26px;
          backdrop-filter: saturate(180%) blur(18px);
        }

        h1 {
          position: relative; padding-left: 15px;
          font-weight: bold; line-height: 1.2; color: #333;
          font-size: 32px; margin-top: 0;
        }
        h1::before {
          content: ""; position: absolute;
          left: 0; top: 50%; transform: translateY(-50%);
          width: 4px; height: 26px;
          background-color: #00C2B3; border-radius: 2px;
        }

        h2 {
          position: relative; padding-left: 15px;
          font-weight: bold; line-height: 1.2; color: #333;
          font-size: 24px; margin-top: 36px; margin-bottom: 14px;
        }
        h2::before {
          content: ""; position: absolute;
          left: 0; top: 50%; transform: translateY(-50%);
          width: 2px; height: 18px;
          background-color: rgba(0, 194, 179, 0.35); border-radius: 1px;
        }

        .lede {
          color: #555;
          margin: 6px 0 28px;
        }

        p { margin: 10px 0 16px; color: #333; }

        hr {
          border: none; border-top: 1px solid #eee;
          margin: 32px 0;
        }

        /* Email subscribe form */
        .sub-form {
          display: flex; gap: 10px; flex-wrap: wrap;
          margin: 14px 0 0;
        }
        .sub-form input[type="email"] {
          flex: 1 1 240px;
          min-width: 0;
          padding: 10px 14px;
          background: #fff;
          border: 1px solid #d2d2d7;
          border-radius: 8px;
          font-size: 17px; color: #1d1d1f;
          font-family: inherit;
          outline: none;
          transition: border-color 0.15s, box-shadow 0.15s;
        }
        .sub-form input[type="email"]:focus {
          border-color: #00C2B3;
          box-shadow: 0 0 0 3px rgba(0, 194, 179, 0.15);
        }
        .sub-form button {
          padding: 10px 24px;
          background: #00C2B3;
          color: #fff;
          border: none;
          border-radius: 8px;
          font-size: 17px; font-weight: 500;
          cursor: pointer;
          font-family: inherit;
          transition: background 0.15s;
        }
        .sub-form button:hover { background: #00A89B; }
        .sub-form button:disabled { background: #b8d9d6; cursor: not-allowed; }

        .sub-status {
          margin: 12px 0 0;
          padding: 10px 14px;
          border-radius: 8px;
          font-size: 15px;
          line-height: 1.6;
        }
        .sub-status[data-kind="ok"]   { background: #f0fdfa; color: #008F84; border: 1px solid #ccf2ec; }
        .sub-status[data-kind="warn"] { background: #fffbeb; color: #b45309; border: 1px solid #fde68a; }
        .sub-status[data-kind="err"]  { background: #fef2f2; color: #b91c1c; border: 1px solid #fecaca; }

        /* RSS URL display */
        .rss-url {
          display: inline-block;
          margin-top: 6px;
          padding: 8px 14px;
          background: #f5f5f7;
          border-radius: 8px;
          font-family: "SF Mono", "Fira Code", Menlo, monospace;
          font-size: 15px; color: #008F84;
          user-select: all;
          word-break: break-all;
        }

        a { color: #008F84; text-decoration: none; transition: opacity 0.2s; }
        a:hover { opacity: 0.7; }

        ol.entries { list-style: none; padding: 0; margin: 0; }
        ol.entries > li {
          padding: 16px 0;
          border-top: 1px solid #eee;
        }
        ol.entries > li:first-child { border-top: none; padding-top: 0; }
        ol.entries > li:last-child { padding-bottom: 0; }

        .entry-title {
          font-size: 17px; font-weight: 600;
          margin: 0 0 4px; color: #1a1a1a;
        }
        .entry-title a { color: inherit; }
        .entry-title a:hover { color: #008F84; opacity: 1; }

        .entry-meta {
          font-size: 13px; color: #8e8e93;
          font-variant-numeric: tabular-nums;
        }

        #footer {
          width: min(90%, 840px);
          margin: 0 auto 28px;
          padding: 0 8px 8px;
          text-align: center;
          font-size: 13px; line-height: 1.65;
          color: #8e8e93;
        }
        #footer a { color: #008F84; }
        .footer-meta { margin-bottom: 4px; }

        @media (max-width: 1150px) {
          #logo { position: absolute !important; top: 30px; left: 20px; }
          #content { margin-top: 110px; padding: 34px 28px 28px; }
        }
        @media (max-width: 767px) {
          #content { margin-top: 108px; width: calc(100% - 32px); padding: 28px 18px 24px; border-radius: 20px; }
          h1 { font-size: 28px; }
        }
      </style>
    </head>
    <body>
      <a href="https://daily.hiwd.com/" id="logo" aria-label="返回 daily 首页">
        <span id="logo-hiwd">h<span id="logo-i">ı</span>wd</span>
        <span id="logo-rule"></span>
        <span id="logo-sub">daily</span>
      </a>

      <div id="content">
        <h1>订阅 hiwd daily</h1>
        <p class="lede"><xsl:value-of select="description" /></p>

        <h2>邮箱订阅</h2>
        <p>每天早上一封当日 AI 简报，直接送到你的邮箱。</p>
        <form class="sub-form" id="email-sub" onsubmit="return false;">
          <input type="email" id="email-input" placeholder="your@email.com" required="required" />
          <button type="submit" id="email-submit">订阅</button>
        </form>
        <p class="sub-status" id="email-status" hidden="hidden"></p>
        <p class="lede">订阅后会向该邮箱发一封确认邮件，点击其中链接即生效；任何时候都可在邮件底部退订。</p>

        <hr />

        <h2>RSS 订阅</h2>
        <p>复制下面的地址粘到你的 RSS 阅读器（Feedly / NetNewsWire / Reeder / Inoreader / Follow 等）：</p>
        <p><span class="rss-url"><xsl:value-of select="atom:link/@href" /></span></p>

        <hr />

        <h2>历史简报</h2>

        <ol class="entries">
          <xsl:for-each select="item">
            <li>
              <div class="entry-title">
                <a>
                  <xsl:attribute name="href"><xsl:value-of select="link" /></xsl:attribute>
                  <xsl:value-of select="title" />
                </a>
              </div>
              <div class="entry-meta">
                <xsl:value-of select="pubDate" />
              </div>
            </li>
          </xsl:for-each>
        </ol>
      </div>

      <div id="footer">
        <div class="footer-meta">由 Claude + Web Search 自动生成 ｜ <a href="/rss.xml">订阅</a></div>
        <div>© 2026 <a href="https://hiwd.com/">hiwd</a> · All rights reserved.</div>
      </div>
      <script><![CDATA[
        (function () {
          var API = 'https://mail.hiwd.com/subscribe';
          var form = document.getElementById('email-sub');
          var input = document.getElementById('email-input');
          var btn = document.getElementById('email-submit');
          var status = document.getElementById('email-status');
          if (!form || !input || !btn || !status) return;

          function showStatus(kind, msg) {
            status.hidden = false;
            status.setAttribute('data-kind', kind);
            status.textContent = msg;
          }
          function setBusy(busy) {
            btn.disabled = busy;
            btn.textContent = busy ? '提交中…' : '订阅';
          }
          var MESSAGES = {
            subscribed:        ['ok',   '已订阅成功，明早起每天送达。'],
            confirmation_sent: ['ok',   '已向该邮箱发送确认邮件，点击其中链接即生效（5 分钟内未到请检查垃圾箱）。'],
            already_subscribed:['warn', '该邮箱已在订阅列表中，无需重复。'],
            resubscribed:      ['ok',   '欢迎回来，已恢复订阅。'],
            invalid_email:     ['err',  '邮箱格式无效，请检查后重试。'],
          };

          form.addEventListener('submit', async function (e) {
            e.preventDefault();
            var email = (input.value || '').trim();
            if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
              showStatus('err', '请输入有效的邮箱地址。');
              input.focus();
              return;
            }
            setBusy(true);
            try {
              var r = await fetch(API, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email: email })
              });
              var j = {};
              try { j = await r.json(); } catch (_) {}
              if (r.ok && j.ok) {
                var pair = MESSAGES[j.status] || ['ok', '订阅请求已提交。'];
                showStatus(pair[0], pair[1]);
                if (j.status === 'subscribed' || j.status === 'confirmation_sent' || j.status === 'resubscribed') {
                  input.value = '';
                }
              } else {
                var errPair = MESSAGES[j.error] || ['err', '提交失败：' + (j.error || ('HTTP ' + r.status)) + '，请稍后再试。'];
                showStatus(errPair[0], errPair[1]);
              }
            } catch (err) {
              showStatus('err', '网络错误，请检查连接后重试。');
            } finally {
              setBusy(false);
            }
          });
        })();
      ]]></script>
    </body>
    </html>
  </xsl:template>
</xsl:stylesheet>
