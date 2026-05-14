/**
 * js/bootstrap.js
 *
 * 职责：event 名小写化 patch + NovaView/Dom 解构 + COLORS + API/HEALTH + apiGet
 * 依赖：NovaView (CDN)
 * 注册：__viz.NV, __viz.DomTags, __viz.COLORS, __viz.API, __viz.HEALTH, __viz.apiGet
 */
(function () {
  'use strict';

  // Patch: nova-dom registers events as "Click" instead of "click" (slice(2) without toLowerCase)
  var _origAdd = HTMLElement.prototype.addEventListener;
  HTMLElement.prototype.addEventListener = function(t, l, o) { return _origAdd.call(this, t.toLowerCase(), l, o); };
  var _origRemove = HTMLElement.prototype.removeEventListener;
  HTMLElement.prototype.removeEventListener = function(t, l, o) { return _origRemove.call(this, t.toLowerCase(), l, o); };

  // ---------- (a) 从 NovaView 解构 API ----------
  const {
    ref, reactive, computed, watch, watchEffect, effect, batch,
    Dom, component, mount,
    Show, For, Switch,
    onMounted, onUnmounted,
  } = NovaView;
  const {
    div, span, button, input, h2, h3, h4, p, ul, li, a,
    canvas, section, main, header,
  } = Dom;

  // ---------- (b) 常量 ----------
  const COLORS = {
    active: '#58a6ff',
    stale: '#d29922',
    archived: '#6e7681',
    superseded: '#a371f7',
    related: 'rgba(88,166,255,0.3)',
    contradicts: '#f85149',
    auto_related: 'rgba(210,153,34,0.4)',
  };

  // ---------- (c) API 层 ----------
  const _origin = (location.protocol === 'file:')
    ? 'http://127.0.0.1:8787'
    : location.origin;
  const API = _origin + '/api/v1';
  const HEALTH = _origin + '/health';

  async function apiGet(url) {
    const resp = await fetch(url);
    if (!resp.ok) {
      const body = await resp.text().catch(() => '');
      throw new Error('HTTP ' + resp.status + (body ? ' · ' + body.slice(0, 120) : ''));
    }
    return resp.json();
  }

  // ---- 注册到 __viz ----
  window.__viz = window.__viz || {};
  window.__viz.NV = {
    ref, reactive, computed, watch, watchEffect, effect, batch,
    Dom, component, mount, Show, For, Switch, onMounted, onUnmounted,
  };
  window.__viz.DomTags = {
    div, span, button, input, h2, h3, h4, p, ul, li, a,
    canvas, section, main, header,
  };
  window.__viz.COLORS = COLORS;
  window.__viz.API = API;
  window.__viz.HEALTH = HEALTH;
  window.__viz.apiGet = apiGet;
})();
