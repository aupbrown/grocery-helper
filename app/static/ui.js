// Coach UI enhancements (CLAUDE_CODE_SPEC.md §6). Everything here is optional:
// each feature no-ops when its data-* hook is absent, and every flow works
// without JS (stacked panels, in-page sheet sections, meta-refresh loading).
(function () {
  'use strict';
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  /* 1 · Sheets & dialogs -------------------------------------------------- */
  function bindSheets(root) {
    $$('[data-sheet-open]', root).forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        var dlg = $(btn.getAttribute('data-sheet-open'));
        if (!dlg || !dlg.showModal) return;
        e.preventDefault();
        dlg.showModal();
      });
    });
    $$('dialog', root).forEach(function (dlg) {
      if (dlg._bound) return;
      dlg._bound = true;
      dlg.addEventListener('click', function (e) {
        if (e.target === dlg) dlg.close();          // backdrop click
      });
      $$('[data-sheet-close]', dlg).forEach(function (b) {
        b.addEventListener('click', function () { dlg.close(); });
      });
    });
  }

  /* 2 · Tabs (Budget-first / Protein-first) ------------------------------- */
  var tablist = $('.tabs[role="tablist"]');
  if (tablist) {
    var tabs = $$('[role="tab"]', tablist);
    var select = function (tab, focus) {
      tabs.forEach(function (t) {
        var on = t === tab;
        t.setAttribute('aria-selected', on ? 'true' : 'false');
        t.tabIndex = on ? 0 : -1;
        var panel = document.getElementById(t.getAttribute('aria-controls'));
        if (panel) panel.classList.toggle('is-active', on);
      });
      $$('[data-plan-kind-input]').forEach(function (inp) {
        inp.value = tab.getAttribute('data-kind') || 'budget';
      });
      if (focus) tab.focus();
      try { sessionStorage.setItem('gb-tab', tab.id); } catch (e) {}
    };
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
    var saved = null;
    try { saved = sessionStorage.getItem('gb-tab'); } catch (e) {}
    var start = saved && document.getElementById(saved);
    select(start && tabs.indexOf(start) !== -1 ? start : tabs[0]);
  }

  /* 3 · Budget slider → live $ + per-meal caption -------------------------- */
  var slider = $('[data-budget-slider]');
  if (slider) {
    var out = $('output[for="' + slider.id + '"]');
    var caption = $('[data-budget-caption]');
    var describe = function (v) {
      var per = v / 21;
      var mood = per < 1.5 ? 'very lean — rice & beans territory' :
                 per < 2.25 ? 'tight but doable 💪' :
                 per < 3.25 ? 'comfortable — room for variety' :
                 'roomy — some nice cuts on the menu 🎉';
      return '≈ $' + per.toFixed(2) + ' per meal — ' + mood;
    };
    var update = function () {
      var v = parseInt(slider.value, 10);
      if (out) out.textContent = '$' + v;
      if (caption) caption.textContent = describe(v);
    };
    slider.addEventListener('input', update);
    update();
  }

  /* 4 · Pantry chips reveal amount rows; "+ something else" ---------------- */
  $$('[data-owned-chip]').forEach(function (input) {
    var row = document.getElementById('amount-' + input.value);
    if (!row) return;
    var sync = function () { row.hidden = !input.checked; };
    input.addEventListener('change', sync);
    sync();
  });
  var addBtn = $('[data-add-own]');
  if (addBtn) {
    addBtn.addEventListener('click', function () {
      var row = document.createElement('div');
      row.className = 'amount-row';
      row.innerHTML = '<input type="text" name="owned_custom" placeholder="What do you have?" ' +
        'aria-label="Something else you own" style="flex:1;text-align:left;width:auto">';
      addBtn.parentNode.insertBefore(row, addBtn.nextSibling);
      row.firstChild.focus();
    });
  }

  /* 5 · Generation polling + rotating status lines ------------------------- */
  if (document.body.hasAttribute('data-poll')) {
    var lineEl = $('[data-status-lines]');
    if (lineEl) {
      var lines = JSON.parse(lineEl.getAttribute('data-status-lines'));
      var li = 0;
      setInterval(function () {
        li = (li + 1) % lines.length;
        lineEl.textContent = lines[li];
      }, 4000);
    }
    var poll = function () {
      fetch('/plan/status').then(function (r) { return r.json(); }).then(function (s) {
        if (s.state === 'done') location.href = '/plan';
        else if (s.state === 'failed') location.href = '/plan/failed';
      }).catch(function () {});
    };
    setInterval(poll, 2000);
    poll();
  }

  /* 6 · Shopping-list checkbox persistence --------------------------------- */
  $$('[data-list-key]').forEach(function (list) {
    var key = list.getAttribute('data-list-key');
    var boxes = $$('input[type="checkbox"]', list);
    var done = [];
    try { done = JSON.parse(localStorage.getItem(key) || '[]'); } catch (e) {}
    boxes.forEach(function (box) {
      if (done.indexOf(box.value) !== -1) box.checked = true;
      box.addEventListener('change', function () {
        var now = boxes.filter(function (b) { return b.checked; })
                       .map(function (b) { return b.value; });
        try { localStorage.setItem(key, JSON.stringify(now)); } catch (e) {}
      });
    });
  });

  /* 7 · Share / copy list --------------------------------------------------- */
  $$('[data-copy-target]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var src = $(btn.getAttribute('data-copy-target'));
      if (!src) return;
      var text = src.value || src.textContent;
      var flash = function () {
        var old = btn.textContent;
        btn.textContent = 'Copied ✓';
        setTimeout(function () { btn.textContent = old; }, 1500);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(flash);
      } else {
        var ta = document.createElement('textarea');
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand('copy'); flash(); } catch (e) {}
        document.body.removeChild(ta);
      }
    });
  });

  /* Reopen a sheet the server flagged (e.g. save-sheet validation error) ---- */
  var auto = $('dialog[data-open-on-load]');
  if (auto && auto.showModal) auto.showModal();

  /* 8 · Feedback dialog (first save only) ----------------------------------- */
  var fb = $('dialog[data-feedback]');
  if (fb && fb.showModal) {
    var fbDone = false;
    try { fbDone = !!localStorage.getItem('fb_done'); } catch (e) {}
    var markDone = function () { try { localStorage.setItem('fb_done', '1'); } catch (e) {} };
    if (!fbDone) setTimeout(function () { fb.showModal(); }, 800);
    $$('.rating-row button', fb).forEach(function (btn) {
      btn.addEventListener('click', function () {
        $$('.rating-row button', fb).forEach(function (b) { b.setAttribute('aria-pressed', 'false'); });
        btn.setAttribute('aria-pressed', 'true');
        var inp = $('input[name="rating"]', fb);
        if (inp) inp.value = btn.getAttribute('data-rating');
      });
    });
    fb.addEventListener('close', function () {
      markDone();
      if (!fb._sent) {
        var body = new FormData();
        body.append('dismissed', '1');
        fetch('/feedback', { method: 'POST', body: body }).catch(function () {});
      }
    });
    var form = $('form', fb);
    if (form) {
      form.addEventListener('submit', function (e) {
        e.preventDefault();
        fb._sent = true;
        fetch('/feedback', { method: 'POST', body: new FormData(form) }).catch(function () {});
        markDone();
        fb.close();
      });
    }
  }

  /* 9 · Meal swap: fetch the fresh card, swap it in ------------------------- */
  function bindSwaps(root) {
    $$('form[data-swap]', root).forEach(function (form) {
      if (form._bound) return;
      form._bound = true;
      form.addEventListener('submit', function (e) {
        var unit = form.closest('.swap-unit');
        if (!unit || !window.fetch) return;        // fall back to full-page POST
        e.preventDefault();
        var dlg = form.closest('dialog');
        if (dlg && dlg.open) dlg.close();
        var card = $('.meal-card', unit);
        if (card) card.classList.add('is-busy');
        var btn = $('button[type="submit"]', form);
        if (btn) { btn.disabled = true; btn.setAttribute('aria-busy', 'true'); }
        fetch(form.action, {
          method: 'POST',
          headers: { 'X-Partial': '1' },
          body: new FormData(form)
        }).then(function (r) {
          if (!r.ok) throw new Error(r.status);
          return r.text();
        }).then(function (html) {
          var tpl = document.createElement('template');
          tpl.innerHTML = html.trim();
          var fresh = tpl.content.firstElementChild;
          unit.parentNode.replaceChild(fresh, unit);
          bindSheets(fresh);
          bindSwaps(fresh);
        }).catch(function () { form.submit(); });
      });
    });
  }

  bindSheets(document);
  bindSwaps(document);
})();
