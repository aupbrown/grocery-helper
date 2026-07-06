// Plan tabs: progressive enhancement. Without this script both plan panels show
// (the .js class + CSS collapse the inactive one only when JS is present). Bars are
// pure CSS, so this file is only about the tablist.
(function () {
  var tablist = document.querySelector('.tabs[role="tablist"]');
  if (!tablist) return;
  var tabs = Array.prototype.slice.call(tablist.querySelectorAll('[role="tab"]'));
  if (tabs.length < 2) return;
  var KEY = 'gh-plan-tab';

  function panelFor(tab) {
    return document.getElementById(tab.getAttribute('aria-controls'));
  }

  function select(tab, focus) {
    tabs.forEach(function (t) {
      var on = t === tab;
      t.setAttribute('aria-selected', on ? 'true' : 'false');
      t.tabIndex = on ? 0 : -1;
      var panel = panelFor(t);
      if (panel) panel.classList.toggle('is-inactive', !on);
    });
    if (focus) tab.focus();
    try { sessionStorage.setItem(KEY, tab.id); } catch (e) {}
  }

  tabs.forEach(function (tab, i) {
    tab.addEventListener('click', function () { select(tab); });
    tab.addEventListener('keydown', function (e) {
      var next = null;
      if (e.key === 'ArrowRight' || e.key === 'ArrowDown') next = tabs[(i + 1) % tabs.length];
      else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') next = tabs[(i - 1 + tabs.length) % tabs.length];
      else if (e.key === 'Home') next = tabs[0];
      else if (e.key === 'End') next = tabs[tabs.length - 1];
      if (next) { e.preventDefault(); select(next, true); }
    });
  });

  // Restore the last-viewed tab (e.g. after regenerating a meal reloads the page).
  var saved = null;
  try { saved = sessionStorage.getItem(KEY); } catch (e) {}
  var start = saved && document.getElementById(saved);
  select(start && tabs.indexOf(start) !== -1 ? start : tabs[0]);
})();
