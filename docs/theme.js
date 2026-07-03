(function () {
  var storageKey = 'hiwd-theme';
  var root = document.documentElement;
  var choices = { light: true, dark: true };
  var mediaQuery = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)');

  function systemTheme() {
    return mediaQuery && mediaQuery.matches ? 'dark' : 'light';
  }

  function readPreference() {
    try {
      var stored = window.localStorage.getItem(storageKey);
      return choices[stored] ? stored : null;
    } catch (error) {
      return null;
    }
  }

  function writePreference(theme) {
    try {
      if (choices[theme] && theme !== systemTheme()) {
        window.localStorage.setItem(storageKey, theme);
      } else {
        window.localStorage.removeItem(storageKey);
      }
    } catch (error) {
      return;
    }
  }

  function effectiveTheme() {
    return readPreference() || systemTheme();
  }

  function paintControls() {
    var currentTheme = effectiveTheme();
    var nextTheme = currentTheme === 'dark' ? 'light' : 'dark';
    var buttons = document.querySelectorAll('[data-theme-toggle]');

    buttons.forEach(function (button) {
      var label = nextTheme === 'dark' ? '夜间' : '白天';
      var ariaLabel = '切换到' + label + '模式';

      if (button.textContent.trim() !== label) {
        button.textContent = label;
      }

      if (button.dataset.themeTarget !== nextTheme) {
        button.dataset.themeTarget = nextTheme;
      }

      if (button.getAttribute('aria-label') !== ariaLabel) {
        button.setAttribute('aria-label', ariaLabel);
      }
    });
  }

  function applyPreference() {
    var preference = readPreference();
    if (preference) {
      root.setAttribute('data-theme', preference);
    } else {
      root.removeAttribute('data-theme');
    }

    paintControls();
  }

  function setTheme(theme) {
    if (!choices[theme]) return;
    writePreference(theme);
    applyPreference();
  }

  applyPreference();

  document.addEventListener('click', function (event) {
    var target = event.target;
    if (!target || !target.closest) return;

    var button = target.closest('[data-theme-toggle]');
    if (!button) return;
    setTheme(button.dataset.themeTarget || (effectiveTheme() === 'dark' ? 'light' : 'dark'));
  });

  document.addEventListener('DOMContentLoaded', function () {
    paintControls();

    if (window.MutationObserver) {
      var observer = new MutationObserver(function () {
        paintControls();
      });

      observer.observe(document.body, { childList: true });
    }
  });

  if (mediaQuery) {
    var handleSystemChange = function () {
      if (!readPreference()) applyPreference();
    };

    if (mediaQuery.addEventListener) {
      mediaQuery.addEventListener('change', handleSystemChange);
    } else if (mediaQuery.addListener) {
      mediaQuery.addListener(handleSystemChange);
    }
  }

  window.hiwdTheme = {
    get: function () {
      return readPreference() || 'system';
    },
    effective: effectiveTheme,
    set: setTheme,
    reset: function () {
      writePreference(systemTheme());
      applyPreference();
    }
  };
}());
