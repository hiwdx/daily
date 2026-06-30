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

        .lede {
          color: #555; font-size: 15px; line-height: 1.7;
          margin: 6px 0 22px;
        }

        .subscribe-card {
          margin: 0 0 14px;
          padding: 14px 16px;
          border: 1px solid rgba(0, 194, 179, 0.18);
          border-left: 3px solid rgba(0, 194, 179, 0.6);
          border-radius: 10px;
          background: linear-gradient(180deg, #fafdfd 0%, #f7fbfb 100%);
          font-size: 14px; color: #444;
        }
        .subscribe-card .url {
          display: inline-block;
          margin-top: 6px;
          padding: 6px 10px;
          background: #fff;
          border: 1px solid #e5e7eb;
          border-radius: 6px;
          font-family: "SF Mono", "Fira Code", monospace;
          font-size: 13px; color: #008F84;
          user-select: all;
        }

        .email-card {
          margin: 0 0 14px;
          padding: 14px 16px;
          border: 1px solid rgba(0, 194, 179, 0.18);
          border-left: 3px solid rgba(0, 194, 179, 0.6);
          border-radius: 10px;
          background: linear-gradient(180deg, #fafdfd 0%, #f7fbfb 100%);
          font-size: 14px; color: #444;
        }
        .email-card form {
          display: flex; gap: 8px; flex-wrap: wrap;
          margin-top: 10px;
        }
        .email-card input[type="email"] {
          flex: 1 1 220px;
          min-width: 0;
          padding: 8px 12px;
          background: #fff;
          border: 1px solid #e5e7eb;
          border-radius: 8px;
          font-size: 14px; color: #1d1d1f;
          font-family: inherit;
          outline: none; transition: border-color 0.15s, box-shadow 0.15s;
        }
        .email-card input[type="email"]:focus {
          border-color: #00C2B3;
          box-shadow: 0 0 0 3px rgba(0, 194, 179, 0.18);
        }
        .email-card button {
          padding: 8px 18px;
          background: #00C2B3;
          color: #fff;
          border: none;
          border-radius: 8px;
          font-size: 14px; font-weight: 500;
          cursor: pointer;
          font-family: inherit;
          transition: background 0.15s;
        }
        .email-card button:hover { background: #00A89B; }
        .email-card button:disabled { background: #b8d9d6; cursor: not-allowed; }
        .email-card .hint {
          margin-top: 10px; font-size: 17px; color: #555; line-height: 1.7;
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

        <div class="email-card">
          <strong>📬 邮箱订阅</strong>　每天早上 9 点收到一封当日 AI 简报。
          <form id="email-sub" onsubmit="return false;">
            <input type="email" id="email-input" placeholder="your@email.com" required="required" />
            <button type="submit" id="email-submit">订阅</button>
          </form>
          <div class="hint">
            点击订阅会自动调起你本机的邮件应用并预填一封确认邮件，发送即可完成订阅；
            随时可在邮件底部链接退订。
          </div>
        </div>

        <div class="subscribe-card">
          <strong>📡 RSS 订阅</strong>　复制下面的地址粘到你的 RSS 阅读器（Feedly / NetNewsWire / Reeder / Inoreader / Follow 等）：
          <br />
          <span class="url"><xsl:value-of select="atom:link/@href" /></span>
        </div>

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
          var form = document.getElementById('email-sub');
          var input = document.getElementById('email-input');
          var btn = document.getElementById('email-submit');
          if (!form || !input || !btn) return;
          form.addEventListener('submit', function (e) {
            e.preventDefault();
            var v = (input.value || '').trim();
            if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v)) {
              input.focus();
              return;
            }
            var subject = encodeURIComponent('[AGENT] subscribe');
            var body = encodeURIComponent(
              'Hi，我想订阅 hiwd daily 每日 AI 简报。\n\n' +
              'email: ' + v + '\n'
            );
            window.location.href = 'mailto:iworld@agent.qq.com?subject=' + subject + '&body=' + body;
            btn.textContent = '已打开邮件客户端';
            btn.disabled = true;
            setTimeout(function () {
              btn.textContent = '订阅';
              btn.disabled = false;
            }, 4000);
          });
        })();
      ]]></script>
    </body>
    </html>
  </xsl:template>
</xsl:stylesheet>
