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
      <script src="/theme.js?v=20260704-1"></script>
      <link rel="stylesheet" type="text/css" href="/style.css?v=20260707-1" />
    </head>
    <body>
      <a href="https://hiwd.com/" id="logo" aria-label="返回 hiwd 主站"></a>

      <div id="content">
        <div class="hero">
          <div class="hero-meta"><a href="https://hiwd.com/">hiwd</a><span class="hero-divider">/</span><a href="/">daily</a><span class="hero-date">订阅</span></div>
          <h1>订阅 hiwd daily</h1>
        </div>

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
        <div class="footer-meta">由 Claude + Web Search 自动生成</div>
        <div>© 2026 <a href="https://hiwd.com/">hiwd</a> · All rights reserved. <button class="theme-toggle" type="button" data-theme-toggle>夜间</button></div>
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
            invalid_email:     ['err',  '邮箱格式无效，请检查后重试。']
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
