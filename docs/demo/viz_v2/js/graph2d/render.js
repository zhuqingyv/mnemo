/**
 * js/graph2d/render.js — 2D canvas 每帧绘制（edges batched → nodes → label sprites → pulses）
 * 注册：__viz.g2d.render.{drawGraph}
 * 优化：label sprite 缓存取代每帧 fillText；视口剔除节点/边；同 style related 边分批 stroke。
 */
(function () {
  'use strict';
  const state = window.__viz.state;
  const _graphMaps = window.__viz._graphMaps;
  const { HALO_DURATION_MS, PULSE_DURATION_MS, PULSE_MAX_RADIUS } = window.__viz.const;
  const VIEW_PAD = 40; // world-space padding so half-visible nodes still draw

  function drawGraph() {
    const g = state.graph;
    const rawNodes = _graphMaps.rawNodes;
    const rawEdges = _graphMaps.rawEdges;
    const c = document.getElementById('graph-canvas');
    if (!c) return;
    const ctx = c.getContext('2d');
    const w = c.clientWidth;
    const h = c.clientHeight;
    ctx.clearRect(0, 0, w, h);
    // snapshot cam primitives once — avoids 5x Proxy reads per frame later
    const cam = g.cam;
    const camX = cam.x, camY = cam.y, camZoom = cam.zoom;
    ctx.save();
    ctx.translate(camX, camY);
    ctx.scale(camZoom, camZoom);

    // world-space viewport bounds (for culling)
    const vl = -camX / camZoom - VIEW_PAD;
    const vt = -camY / camZoom - VIEW_PAD;
    const vr = (w - camX) / camZoom + VIEW_PAD;
    const vb = (h - camY) / camZoom + VIEW_PAD;

    const searching = (state.searchQuery || '').trim().length > 0 && _graphMaps.matched.size > 0;
    const hoverNode = g.hover;
    const hoverId = hoverNode ? hoverNode.id : null;
    const { brightenColor } = window.__viz.g2d.color;
    const getGlowSprite = window.__viz.g2d.getGlowSprite;
    const getLabelSprite = window.__viz.g2d.getLabelSprite;
    const nodeFillFor = window.__viz.g2d.build.nodeFillFor;
    const truncateTitle = window.__viz.util.truncateTitle;

    // ---- edges: batch "related" by alpha/width; draw special ones (glow/dash/hover) individually ----
    const relatedBuckets = new Map();
    for (const e of rawEdges) {
      const a = _graphMaps.nodeIndex.get(e.s);
      const b = _graphMaps.nodeIndex.get(e.t);
      if (!a || !b) continue;
      // cull edges where both endpoints are off-screen on same side
      if ((a.x < vl && b.x < vl) || (a.x > vr && b.x > vr) ||
          (a.y < vt && b.y < vt) || (a.y > vb && b.y > vb)) continue;
      const type = e.type || 'related';
      let rgb, baseAlpha, width, dash = null, glow = false;
      if (type === 'contradicts') { rgb = '248, 81, 73'; baseAlpha = 0.85; width = 2; glow = true; }
      else if (type === 'supersedes') { rgb = '163, 113, 247'; baseAlpha = 0.75; width = 1.5; dash = [5, 4]; }
      else { rgb = '88, 166, 255'; baseAlpha = 0.3; width = 1.5; }
      let alpha = baseAlpha;
      let drawWidth = width;
      if (searching) {
        if (!(_graphMaps.matched.has(a.id) || _graphMaps.matched.has(b.id))) alpha = 0.1;
      }
      let isHoverEdge = false;
      if (hoverId != null) {
        if (a.id === hoverId || b.id === hoverId) { alpha = 1.0; drawWidth = Math.max(2, width); isHoverEdge = true; }
        else alpha = Math.min(alpha, 0.1);
      }
      const eop = (e.opacity == null) ? 1 : e.opacity;
      alpha *= eop;
      // batchable only if plain related + no dash + no glow + not hover-highlighted
      if (!glow && !dash && !isHoverEdge && type === 'related') {
        const key = rgb + '|' + alpha.toFixed(3) + '|' + drawWidth;
        let bucket = relatedBuckets.get(key);
        if (!bucket) { bucket = { rgb, alpha, width: drawWidth, pts: [] }; relatedBuckets.set(key, bucket); }
        bucket.pts.push(a.x, a.y, b.x, b.y);
        continue;
      }
      ctx.strokeStyle = 'rgba(' + rgb + ',' + alpha + ')';
      ctx.lineWidth = drawWidth;
      ctx.setLineDash(dash || []);
      if (glow) {
        ctx.save();
        ctx.strokeStyle = 'rgba(' + rgb + ',' + Math.min(0.35, alpha * 0.5) + ')';
        ctx.lineWidth = drawWidth + 4;
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
        ctx.restore();
      }
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
    }
    ctx.setLineDash([]);
    for (const bucket of relatedBuckets.values()) {
      ctx.strokeStyle = 'rgba(' + bucket.rgb + ',' + bucket.alpha + ')';
      ctx.lineWidth = bucket.width;
      ctx.beginPath();
      const pts = bucket.pts;
      for (let i = 0; i < pts.length; i += 4) {
        ctx.moveTo(pts[i], pts[i + 1]);
        ctx.lineTo(pts[i + 2], pts[i + 3]);
      }
      ctx.stroke();
    }

    // ---- nodes ----
    const nowMs = performance.now();
    const showLabels = camZoom > 0.55;
    for (const n of rawNodes) {
      if (n.x < vl || n.x > vr || n.y < vt || n.y > vb) continue; // viewport cull
      const status = n.k.status || 'active';
      const dim = searching && !_graphMaps.matched.has(n.id);
      const op = (n.opacity == null) ? 1 : n.opacity;
      const isMatched = _graphMaps.matched.has(n.id);
      let fill = n.fill || nodeFillFor(n.k);
      if (isMatched) fill = brightenColor(fill, 0.3);
      const baseAlpha = dim ? 0.15 : (status === 'superseded' || status === 'archived') ? 0.55 : 1;
      const alpha = baseAlpha * op;
      ctx.globalAlpha = alpha;

      if (n.enteredAt && !n.removing) {
        const age = nowMs - n.enteredAt;
        if (age < HALO_DURATION_MS) {
          const haloT = age / HALO_DURATION_MS;
          const pulse = 0.5 + 0.5 * Math.sin(age / 180);
          ctx.globalAlpha = (1 - haloT) * (0.25 + pulse * 0.25) * op;
          ctx.fillStyle = '#58a6ff';
          ctx.beginPath();
          ctx.arc(n.x, n.y, n.r + 6 + pulse * 4, 0, Math.PI * 2);
          ctx.fill();
          ctx.globalAlpha = alpha;
        }
      }

      if (isMatched) {
        const sprite = getGlowSprite('rgba(163,113,247,0.55)', n.r, 18);
        ctx.drawImage(sprite.canvas, n.x - sprite.half, n.y - sprite.half);
      } else if (n.id === hoverId) {
        const prev = ctx.globalAlpha;
        const sprite = getGlowSprite(fill, n.r, 10);
        ctx.globalAlpha = 0.5 * prev;
        ctx.drawImage(sprite.canvas, n.x - sprite.half, n.y - sprite.half);
        ctx.globalAlpha = prev;
      }

      ctx.beginPath();
      ctx.fillStyle = fill;
      ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
      ctx.fill();
      ctx.lineWidth = 1;
      ctx.strokeStyle = n.id === hoverId ? '#e6edf3' : 'rgba(14,17,22,0.6)';
      ctx.stroke();

      if (showLabels) {
        const labelAlpha = (dim ? 0.25 : 0.85) * op;
        ctx.globalAlpha = labelAlpha;
        const title = truncateTitle(n.k.title || '', 20);
        const sprite = getLabelSprite(title);
        // sprite.canvas 是 dpr 放大的物理像素；5-arg drawImage 按逻辑尺寸缩放输出
        ctx.drawImage(sprite.canvas, n.x + n.r + 4, n.y - sprite.halfH, sprite.w, sprite.h);
      }
      ctx.globalAlpha = 1;
    }

    // ---- pulses ----
    if (g.pulses.length) {
      for (const pu of g.pulses) {
        const node = _graphMaps.nodeIndex.get(pu.nodeId);
        if (!node) continue;
        const age = nowMs - pu.startedAt;
        if (age < 0 || age >= PULSE_DURATION_MS) continue;
        const t = age / PULSE_DURATION_MS;
        const eased = 1 - Math.pow(1 - t, 2);
        const radius = node.r + eased * PULSE_MAX_RADIUS;
        const alpha = (1 - t) * 0.85;
        ctx.globalAlpha = alpha;
        ctx.strokeStyle = 'rgba(251,191,36,0.35)';
        ctx.lineWidth = 6;
        ctx.beginPath(); ctx.arc(node.x, node.y, radius, 0, Math.PI * 2); ctx.stroke();
        ctx.strokeStyle = '#fbbf24';
        ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(node.x, node.y, radius, 0, Math.PI * 2); ctx.stroke();
        if (t < 0.35) {
          const dotAlpha = (1 - t / 0.35) * 0.9;
          ctx.globalAlpha = dotAlpha;
          const sprite = getGlowSprite('rgba(254,243,199,0.9)', node.r * 0.7, 10);
          ctx.drawImage(sprite.canvas, node.x - sprite.half, node.y - sprite.half);
          ctx.fillStyle = '#fef3c7';
          ctx.beginPath(); ctx.arc(node.x, node.y, node.r * 0.7, 0, Math.PI * 2); ctx.fill();
        }
        ctx.globalAlpha = 1;
      }
    }

    ctx.restore();
  }

  // ---- 注册到 __viz 命名空间 ----
  window.__viz.g2d = window.__viz.g2d || {};
  window.__viz.g2d.render = { drawGraph };
})();
