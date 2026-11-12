/* Colour theme: Auto (follows the device), Light or Dark. Saved on this device only. Loaded in <head>
   (external file, because the page's content-security policy allows no inline scripts) so the saved
   theme is applied before first paint and dark mode never flashes light. */
(function () {
  var KEY = 'qa-theme';
  var ORDER = ['system', 'light', 'dark'];
  var LABEL = { system: 'Auto', light: 'Light', dark: 'Dark' };
  var media = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;

  function read() {
    try { var v = localStorage.getItem(KEY); return v === 'light' || v === 'dark' ? v : 'system'; } catch (e) { return 'system'; }
  }
  function apply(pref) {
    var dark = pref === 'dark' || (pref === 'system' && media && media.matches);
    document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
    var meta = document.querySelector('meta[name=theme-color]');
    if (meta) meta.setAttribute('content', dark ? '#1b1a16' : '#f6f6f2');
    return dark;
  }
  function paintButtons(pref) {
    var buttons = document.querySelectorAll('[data-theme-toggle]');
    for (var i = 0; i < buttons.length; i++) {
      var b = buttons[i];
      b.setAttribute('aria-label', 'Colour theme: ' + LABEL[pref] + '. Press to change.');
      b.setAttribute('title', 'Theme: ' + LABEL[pref]);
      var text = b.querySelector('[data-theme-label]');
      if (text) text.textContent = LABEL[pref];
    }
  }
  var pref = read();
  apply(pref);
  if (media && media.addEventListener) {
    media.addEventListener('change', function () { if (read() === 'system') apply('system'); });
  }
  document.addEventListener('DOMContentLoaded', function () {
    paintButtons(pref);
    var buttons = document.querySelectorAll('[data-theme-toggle]');
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].addEventListener('click', function () {
        pref = ORDER[(ORDER.indexOf(pref) + 1) % ORDER.length];
        try { localStorage.setItem(KEY, pref); } catch (e) { /* per-device convenience only */ }
        apply(pref);
        paintButtons(pref);
      });
    }
  });
})();
