/**
 * ceasefire_widget.js — Atlas Ceasefire Probability Tracker Widget
 *
 * Standalone module that renders ceasefire probability into the Monitor tab.
 * Reads data from window._rawData.ceasefire (injected by generate_data.py).
 *
 * Loading pattern: <script src="ceasefire_widget.js"></script>
 * - Self-contained IIFE, no external deps
 * - Hooks into existing switchTab + render functions
 * - Injects its own section DOM into #tab-monitor
 */
(function () {
  'use strict';

  // ── Probability colour scale ─────────────────────────────────────────────
  var PROB_BANDS = [
    { max: 15,  color: '#d05858', label: 'VERY UNLIKELY' },
    { max: 30,  color: '#d97a55', label: 'UNLIKELY'      },
    { max: 50,  color: '#d4a84a', label: 'COIN FLIP'     },
    { max: 70,  color: '#7fb858', label: 'POSSIBLE'      },
    { max: 100, color: '#55b0a5', label: 'LIKELY'        },
  ];

  function getBand(prob) {
    for (var i = 0; i < PROB_BANDS.length; i++) {
      if (prob <= PROB_BANDS[i].max) return PROB_BANDS[i];
    }
    return PROB_BANDS[PROB_BANDS.length - 1];
  }

  // ── Inject section CSS ───────────────────────────────────────────────────
  function injectStyles() {
    if (document.getElementById('cf-widget-styles')) return;
    var style = document.createElement('style');
    style.id = 'cf-widget-styles';
    style.textContent = [
      '#cf-section { margin-bottom: 14px; }',
      '#cf-section .section { margin-bottom: 0; }',

      /* widget body layout */
      '.cf-body { display: grid; grid-template-columns: auto 1fr; gap: 20px; align-items: start; }',
      '@media (max-width: 700px) { .cf-body { grid-template-columns: 1fr; } }',

      /* probability display */
      '.cf-prob-block { display: flex; flex-direction: column; align-items: center; min-width: 140px; }',
      '.cf-prob-num { font-family: var(--mono); font-size: 64px; font-weight: 700; line-height: 1; letter-spacing: -2px; font-variant-numeric: tabular-nums; }',
      '.cf-prob-pct { font-family: var(--mono); font-size: 20px; font-weight: 400; color: var(--text-tertiary); margin-left: 2px; vertical-align: super; font-size: 28px; }',
      '.cf-prob-label { font-family: var(--mono); font-size: 11px; font-weight: 600; letter-spacing: 2px; margin-top: 6px; }',
      '.cf-prob-bar { width: 100%; height: 4px; background: var(--border); border-radius: 2px; margin-top: 10px; overflow: hidden; }',
      '.cf-prob-bar-fill { height: 100%; border-radius: 2px; transition: width 0.6s cubic-bezier(0.19,1,0.22,1); }',

      /* info side */
      '.cf-info { display: flex; flex-direction: column; gap: 10px; }',
      '.cf-row { display: flex; gap: 8px; font-size: 12px; }',
      '.cf-row-label { color: var(--text-tertiary); font-family: var(--mono); min-width: 90px; flex-shrink: 0; }',
      '.cf-row-value { color: var(--text); }',
      '.cf-action { font-family: var(--mono); font-size: 12px; color: var(--text-secondary); background: var(--surface-raised); border: 1px solid var(--border); border-radius: 6px; padding: 8px 12px; }',

      /* signal counts */
      '.cf-signals { display: flex; gap: 10px; }',
      '.cf-signal-pill { font-family: var(--mono); font-size: 11px; padding: 3px 10px; border-radius: 20px; font-weight: 600; }',
      '.cf-signal-cease { background: rgba(85,176,165,0.12); color: #55b0a5; }',
      '.cf-signal-esc { background: rgba(208,88,88,0.10); color: #d05858; }',

      /* change log */
      '.cf-changelog { margin-top: 14px; }',
      '.cf-changelog-title { font-family: var(--mono); font-size: 10px; font-weight: 600; color: var(--text-tertiary); letter-spacing: 1.5px; text-transform: uppercase; margin-bottom: 6px; }',
      '.cf-log-item { display: flex; gap: 8px; font-size: 11px; padding: 4px 0; border-bottom: 1px solid var(--border-subtle); align-items: baseline; }',
      '.cf-log-item:last-child { border-bottom: none; }',
      '.cf-log-ts { font-family: var(--mono); color: var(--text-tertiary); white-space: nowrap; flex-shrink: 0; }',
      '.cf-log-arrow { color: var(--text-tertiary); flex-shrink: 0; }',
      '.cf-log-label { color: var(--text-secondary); }',
      '.cf-log-state-on { color: #55b0a5; font-weight: 600; }',
      '.cf-log-state-off { color: var(--text-tertiary); }',

      /* expandable factors */
      '.cf-factors-toggle { font-family: var(--mono); font-size: 11px; color: var(--blue); cursor: pointer; margin-top: 12px; display: inline-flex; align-items: center; gap: 4px; user-select: none; }',
      '.cf-factors-toggle:hover { color: var(--text-secondary); }',
      '.cf-factors-detail { display: none; margin-top: 10px; }',
      '.cf-factors-detail.open { display: block; }',
      '.cf-category { margin-bottom: 10px; }',
      '.cf-category-title { font-family: var(--mono); font-size: 10px; font-weight: 600; color: var(--text-tertiary); letter-spacing: 1.5px; text-transform: uppercase; padding-bottom: 4px; border-bottom: 1px solid var(--border-subtle); margin-bottom: 6px; }',
      '.cf-factor-row { display: grid; grid-template-columns: 16px 1fr auto auto; gap: 6px; align-items: center; padding: 3px 0; font-size: 12px; }',
      '.cf-dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }',
      '.cf-dot-on { background: #55b0a5; box-shadow: 0 0 4px rgba(85,176,165,0.5); }',
      '.cf-dot-off { background: var(--border); }',
      '.cf-dot-esc-on { background: #d05858; box-shadow: 0 0 4px rgba(208,88,88,0.5); }',
      '.cf-factor-label { color: var(--text-secondary); }',
      '.cf-factor-conf { font-family: var(--mono); font-size: 10px; color: var(--text-tertiary); }',
      '.cf-factor-wt { font-family: var(--mono); font-size: 10px; color: var(--text-tertiary); }',

      /* empty state */
      '.cf-empty { color: var(--text-tertiary); font-family: var(--mono); font-size: 12px; padding: 12px 0; }',
    ].join('\n');
    document.head.appendChild(style);
  }

  // ── Inject section placeholder into Monitor tab ─────────────────────────
  function injectSectionDOM() {
    var tabMonitor = document.getElementById('tab-monitor');
    if (!tabMonitor) return;
    if (document.getElementById('cf-section')) return; // already injected

    var sect = document.createElement('div');
    sect.id = 'cf-section';
    sect.innerHTML = [
      '<div class="section animate-in" style="animation-delay:0.10s">',
      '  <div class="section-head" onclick="CeasefireWidget.toggleSection()">',
      '    <div class="section-title">/Ceasefire Probability <span id="cf-badge" class="section-count"></span></div>',
      '    <span class="chevron down" id="chev-cf">▾</span>',
      '  </div>',
      '  <div class="section-body" id="cf-body">',
      '    <div class="cf-empty" id="cf-placeholder">Loading ceasefire data…</div>',
      '  </div>',
      '</div>',
    ].join('');

    // Insert BEFORE the first child of the Monitor tab
    tabMonitor.insertBefore(sect, tabMonitor.firstChild);
  }

  // ── Helpers ───────────────────────────────────────────────────────────────
  function esc(s) {
    return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  function fmtTs(ts) {
    if (!ts) return '—';
    var d = new Date(ts);
    if (isNaN(d)) return ts;
    return d.toLocaleString('en-AU', {
      timeZone: 'Australia/Brisbane',
      day: '2-digit', month: 'short',
      hour: '2-digit', minute: '2-digit',
      hour12: false,
    }) + ' AEST';
  }

  function fmtShortTs(ts) {
    if (!ts) return '—';
    var d = new Date(ts);
    if (isNaN(d)) return ts;
    return d.toLocaleString('en-AU', {
      timeZone: 'Australia/Brisbane',
      day: '2-digit', month: 'short',
      hour: '2-digit', minute: '2-digit',
      hour12: false,
    });
  }

  // ── Render ────────────────────────────────────────────────────────────────
  function render(ceasefire) {
    var body = document.getElementById('cf-body');
    var badge = document.getElementById('cf-badge');
    if (!body) return;
    if (!ceasefire) {
      body.innerHTML = '<div class="cf-empty">No ceasefire data available.</div>';
      return;
    }

    var prob = ceasefire.probability || 0;
    var band = getBand(prob);
    if (badge) {
      badge.textContent = prob + '%';
      badge.style.color = band.color;
    }

    var factors  = ceasefire.factors  || [];
    var changeLog = ceasefire.change_log || [];
    var cfCount  = ceasefire.active_ceasefire_count || 0;
    var escCount = ceasefire.active_escalation_count || 0;
    var lastUpd  = ceasefire.last_updated;
    var label    = ceasefire.probability_label || band.label;
    var timeline = ceasefire.timeline || '—';
    var action   = ceasefire.portfolio_action || '—';

    // ── Main widget HTML ───────────────────────────────────────────
    var html = '<div class="cf-body">';

    // Probability block (left)
    html += '<div class="cf-prob-block">';
    html += '<div class="cf-prob-num" style="color:' + band.color + '">';
    html += prob + '<span class="cf-prob-pct" style="color:' + band.color + ';opacity:0.6">%</span>';
    html += '</div>';
    html += '<div class="cf-prob-label" style="color:' + band.color + '">' + esc(label) + '</div>';
    html += '<div class="cf-prob-bar">';
    html += '<div class="cf-prob-bar-fill" style="width:' + Math.min(prob, 100) + '%;background:' + band.color + '"></div>';
    html += '</div>';
    html += '</div>';

    // Info block (right)
    html += '<div class="cf-info">';
    html += '<div class="cf-row"><span class="cf-row-label">Timeline:</span><span class="cf-row-value">' + esc(timeline) + '</span></div>';
    html += '<div class="cf-row"><span class="cf-row-label">Updated:</span><span class="cf-row-value">' + fmtTs(lastUpd) + '</span></div>';

    // Signals
    html += '<div class="cf-signals">';
    html += '<span class="cf-signal-pill cf-signal-cease">☮ ' + cfCount + ' ceasefire signal' + (cfCount !== 1 ? 's' : '') + '</span>';
    html += '<span class="cf-signal-pill cf-signal-esc">⚔ ' + escCount + ' escalation signal' + (escCount !== 1 ? 's' : '') + '</span>';
    html += '</div>';

    // Action
    html += '<div class="cf-action">▶ ' + esc(action) + '</div>';

    html += '</div>'; // .cf-info
    html += '</div>'; // .cf-body

    // ── Change log ─────────────────────────────────────────────────
    html += '<div class="cf-changelog">';
    html += '<div class="cf-changelog-title">Recent changes</div>';
    var logSlice = changeLog.slice().reverse().slice(0, 5);
    if (logSlice.length === 0) {
      html += '<div style="font-size:11px;color:var(--text-tertiary)">No changes recorded yet.</div>';
    } else {
      logSlice.forEach(function (entry) {
        var oldOn = entry.old_active != null ? entry.old_active : entry.old_state;
        var newOn = entry.new_active != null ? entry.new_active : entry.new_state;
        var oldTxt = oldOn ? '<span class="cf-log-state-on">ON</span>' : '<span class="cf-log-state-off">off</span>';
        var newTxt = newOn ? '<span class="cf-log-state-on">ON</span>' : '<span class="cf-log-state-off">off</span>';
        html += '<div class="cf-log-item">';
        html += '<span class="cf-log-ts">' + fmtShortTs(entry.timestamp) + '</span>';
        html += '<span class="cf-log-arrow">›</span>';
        html += '<span class="cf-log-label">' + esc(entry.factor_label || entry.factor_id || '') + '</span>';
        html += '<span class="cf-log-arrow">:</span>';
        html += oldTxt + ' <span class="cf-log-arrow">→</span> ' + newTxt;
        if (entry.notes) html += ' <span style="color:var(--text-tertiary);font-size:10px">' + esc(entry.notes) + '</span>';
        html += '</div>';
      });
    }
    html += '</div>';

    // ── Expandable factors detail ─────────────────────────────────
    html += '<span class="cf-factors-toggle" onclick="CeasefireWidget.toggleFactors()" id="cf-toggle-btn">';
    html += '<span id="cf-toggle-arrow">▸</span> View all ' + factors.length + ' factors</span>';
    html += '<div class="cf-factors-detail" id="cf-factors-detail">';

    if (factors.length === 0) {
      html += '<div class="cf-empty">No factors defined.</div>';
    } else {
      // Group by category
      var categories = {};
      factors.forEach(function (f) {
        var cat = f.category || 'other';
        if (!categories[cat]) categories[cat] = [];
        categories[cat].push(f);
      });
      Object.keys(categories).sort().forEach(function (cat) {
        html += '<div class="cf-category">';
        html += '<div class="cf-category-title">' + esc(cat) + '</div>';
        categories[cat].forEach(function (f) {
          var isEsc  = f.type === 'escalation';
          var isOn   = !!f.state;
          var dotCls = isOn ? (isEsc ? 'cf-dot-esc-on' : 'cf-dot-on') : 'cf-dot-off';
          html += '<div class="cf-factor-row">';
          html += '<div class="cf-dot ' + dotCls + '"></div>';
          html += '<div class="cf-factor-label">' + esc(f.label || f.id || '') + '</div>';
          html += '<div class="cf-factor-conf">' + esc(f.confidence || '') + '</div>';
          html += '<div class="cf-factor-wt">w' + esc(f.weight || 1) + '</div>';
          html += '</div>';
          if (f.source) {
            html += '<div style="padding-left:22px;font-size:10px;color:var(--text-tertiary);margin-top:-2px;margin-bottom:3px">';
            html += esc(f.source);
            if (f.notes) html += ' — ' + esc(f.notes);
            html += '</div>';
          }
        });
        html += '</div>';
      });
    }

    html += '</div>'; // .cf-factors-detail

    body.innerHTML = html;
  }

  // ── Toggle helpers (called from inline onclick) ────────────────────────
  var _sectionCollapsed = false;
  var _factorsOpen = false;

  window.CeasefireWidget = {
    render: render,

    toggleSection: function () {
      var body = document.getElementById('cf-body');
      var chev = document.getElementById('chev-cf');
      _sectionCollapsed = !_sectionCollapsed;
      if (body) body.classList.toggle('collapsed', _sectionCollapsed);
      if (chev) {
        chev.classList.toggle('down', !_sectionCollapsed);
        chev.classList.toggle('right', _sectionCollapsed);
      }
    },

    toggleFactors: function () {
      _factorsOpen = !_factorsOpen;
      var detail = document.getElementById('cf-factors-detail');
      var arrow  = document.getElementById('cf-toggle-arrow');
      if (detail) detail.classList.toggle('open', _factorsOpen);
      if (arrow) arrow.textContent = _factorsOpen ? '▾' : '▸';
    },
  };

  // ── Bootstrap ──────────────────────────────────────────────────────────
  function tryRender() {
    var data = window._rawData;
    if (data && data.ceasefire) {
      render(data.ceasefire);
    }
  }

  // Hook into switchTab: re-render ceasefire widget when Monitor tab opens
  if (typeof switchTab === 'function') {
    var _prevSwitchTab = switchTab;
    switchTab = function (tab) {
      _prevSwitchTab(tab);
      if (tab === 'monitor') tryRender();
    };
  }

  // Hook into render: update ceasefire widget whenever dashboard data refreshes
  if (typeof render === 'function' && render !== CeasefireWidget.render) {
    var _prevRender = render;
    render = function (d) {
      _prevRender(d);
      if (d && d.ceasefire) {
        CeasefireWidget.render(d.ceasefire);
      }
    };
  }

  // Init on DOM ready
  function init() {
    injectStyles();
    injectSectionDOM();
    tryRender();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
