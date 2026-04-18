/** graph2d/interactions.js — 2D canvas mouse/wheel/resize + hover + drag + zoom */
(function () {
  'use strict';
  const state = window.__viz.state;
  const _graphMaps = window.__viz._graphMaps;
  const { escapeHtml } = window.__viz.util;

  function resizeCanvas() {
    const c = document.getElementById('graph-canvas');
    if (!c) return;
    const dpr = 1; // Cap at 1 — Retina 4x pixel count kills perf
    const w = c.clientWidth;
    const h = c.clientHeight;
    c.width = Math.floor(w * dpr);
    c.height = Math.floor(h * dpr);
    const ctx = c.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    window.__viz.g2d.loop.markRenderDirty();
  }

  function screenToWorld(sx, sy) {
    const cam = state.graph.cam;
    return { x: (sx - cam.x) / cam.zoom, y: (sy - cam.y) / cam.zoom };
  }

  function pickNode(sx, sy) {
    const { x, y } = screenToWorld(sx, sy);
    // iterate raw so returned ref matches physics/render iteration identity
    const nodes = _graphMaps.rawNodes;
    for (let i = nodes.length - 1; i >= 0; i--) {
      const n = nodes[i];
      const dx = n.x - x;
      const dy = n.y - y;
      if (dx * dx + dy * dy <= (n.r + 3) * (n.r + 3)) return n;
    }
    return null;
  }

  let _graphInteractionsWired = false;
  let _cleanupInteractions = null;
  function wireGraphInteractions() {
    // Show 销毁重建后需要重新绑定到新 DOM 元素
    if (_cleanupInteractions) { _cleanupInteractions(); _cleanupInteractions = null; }
    _graphInteractionsWired = false;
    const c = document.getElementById('graph-canvas');
    const tip = document.getElementById('graph-tooltip');
    if (!c || !tip) return;

    const markPhysicsActive = (reason) => window.__viz.g2d.physics.markPhysicsActive(reason);
    const ensureRAF = () => window.__viz.g2d.loop.ensureRAF();

    // rAF-throttled hover pipeline
    let _hoverPending = false;
    let _lastMouse = { sx: 0, sy: 0, inside: false };
    function _flushHover() {
      _hoverPending = false;
      if (!_lastMouse.inside) return;
      const { sx, sy } = _lastMouse;
      const g = state.graph;
      const prevHover = g.hover;
      const n = pickNode(sx, sy);
      g.hover = n;
      if (prevHover !== n) {
        g.hoverDirty = true; ensureRAF();
        if (n) {
          const tags = (n.k.tags || []).slice(0, 5).map(t => '#' + t).join(' ');
          tip.innerHTML =
            '<div class="tt-title">' + escapeHtml(n.k.title || '') + '</div>' +
            '<div class="tt-meta">status=<b>' + escapeHtml(n.k.status || '') + '</b> · scope=<b>' + escapeHtml(n.k.scope || '') + '</b>' +
              (n.k.claim_type ? ' · type=<b>' + escapeHtml(n.k.claim_type) + '</b>' : '') + '</div>' +
            (tags ? '<div class="tt-tags">' + escapeHtml(tags) + '</div>' : '');
          tip.style.display = 'block';
          c.style.cursor = 'pointer';
        } else {
          tip.style.display = 'none';
          c.style.cursor = 'grab';
        }
      }
      if (n) {
        const tw = tip.offsetWidth, th = tip.offsetHeight;
        let left = sx + 14, top = sy + 14;
        if (left + tw > c.clientWidth) left = sx - tw - 14;
        if (top + th > c.clientHeight) top = sy - th - 14;
        tip.style.left = left + 'px';
        tip.style.top = top + 'px';
      }
    }

    c.addEventListener('mousemove', (e) => {
      const rect = c.getBoundingClientRect();
      const sx = e.clientX - rect.left;
      const sy = e.clientY - rect.top;
      const g = state.graph;

      if (g.drag) {
        if (g.drag.node) {
          const { x, y } = screenToWorld(sx, sy);
          g.drag.node.x = x - g.drag.dxOffset;
          g.drag.node.y = y - g.drag.dyOffset;
          g.drag.node.vx = 0;
          g.drag.node.vy = 0;
          markPhysicsActive('drag');
        } else if (g.drag.pan) {
          g.cam.x += (sx - g.drag.lastX);
          g.cam.y += (sy - g.drag.lastY);
          g.drag.lastX = sx;
          g.drag.lastY = sy;
          g.camDirty = true;
          ensureRAF();
        }
        return;
      }

      _lastMouse.sx = sx; _lastMouse.sy = sy; _lastMouse.inside = true;
      if (!_hoverPending) {
        _hoverPending = true;
        requestAnimationFrame(_flushHover);
      }
    });

    c.addEventListener('mouseleave', () => {
      _lastMouse.inside = false;
      if (state.graph.hover) { state.graph.hoverDirty = true; ensureRAF(); }
      state.graph.hover = null;
      tip.style.display = 'none';
    });

    c.addEventListener('mousedown', (e) => {
      const rect = c.getBoundingClientRect();
      const sx = e.clientX - rect.left;
      const sy = e.clientY - rect.top;
      const n = pickNode(sx, sy);
      c.classList.add('dragging');
      if (n) {
        const { x, y } = screenToWorld(sx, sy);
        state.graph.drag = {
          node: n, dxOffset: x - n.x, dyOffset: y - n.y,
          moved: false, startX: sx, startY: sy,
        };
        n.fixed = true;
        markPhysicsActive('drag');
      } else {
        state.graph.drag = { pan: true, lastX: sx, lastY: sy, startX: sx, startY: sy };
        ensureRAF();
      }
    });

    var _onMouseUp = function(e) {
      const d = state.graph.drag;
      const c2 = document.getElementById('graph-canvas');
      if (c2) c2.classList.remove('dragging');
      if (!d) return;
      const rect = c2 ? c2.getBoundingClientRect() : { left: 0, top: 0 };
      const sx = e.clientX - rect.left;
      const sy = e.clientY - rect.top;
      const moved = Math.abs(sx - d.startX) + Math.abs(sy - d.startY) > 4;
      if (d.node) {
        d.node.fixed = false;
        if (!moved) window.__viz.detail.openDetail(d.node.id);
        markPhysicsActive('drag');
      }
      state.graph.drag = null;
      ensureRAF();
    };
    window.addEventListener('mouseup', _onMouseUp);

    c.addEventListener('wheel', (e) => {
      e.preventDefault();
      const rect = c.getBoundingClientRect();
      const sx = e.clientX - rect.left;
      const sy = e.clientY - rect.top;
      const cam = state.graph.cam;
      const prev = cam.zoom;
      const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
      cam.zoom = Math.max(0.25, Math.min(3, cam.zoom * factor));
      const k = cam.zoom / prev;
      cam.x = sx - (sx - cam.x) * k;
      cam.y = sy - (sy - cam.y) * k;
      state.graph.camDirty = true;
      ensureRAF();
    }, { passive: false });

    // debounced resize
    let _resizeTimer = 0;
    var _onResize = function() {
      if (state.view !== '2d') return;
      if (_resizeTimer) clearTimeout(_resizeTimer);
      _resizeTimer = setTimeout(() => { _resizeTimer = 0; resizeCanvas(); }, 100);
    };
    window.addEventListener('resize', _onResize);
    _graphInteractionsWired = true;
    _cleanupInteractions = function() {
      window.removeEventListener('mouseup', _onMouseUp);
      window.removeEventListener('resize', _onResize);
      _graphInteractionsWired = false;
    };
  }
  window.__viz.g2d = window.__viz.g2d || {};
  window.__viz.g2d.interactions = { resizeCanvas, pickNode, wireGraphInteractions };
})();
