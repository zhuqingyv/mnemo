/**
 * js/graph3d/engine.js
 *
 * 职责：3D 图谱引擎初始化 — ForceGraph3D 构造 + 事件链 + d3Force 调优
 * 依赖：__viz.state, __viz._3dMaps, __viz.g3d.style.{nodeColor3D,edgeStyle3D},
 *       __viz.g3d.labels.updateLabels, __viz.g3d.lifecycle.recolorNode,
 *       __viz.detail.openDetail, __viz.util.escapeHtml, 全局 ForceGraph3D
 * 注册：__viz.g3d.initGraph
 *
 * 源行：docs/demo/viz_v2/index.html 3158–3248
 */
(function () {
  'use strict';

  const state = window.__viz.state;
  const _3dMaps = window.__viz._3dMaps;

  function init3DGraph() {
    const container = document.getElementById('graph-3d-container');
    if (!container) return null;
    if (typeof ForceGraph3D === 'undefined') {
      console.warn('[viz] 3d-force-graph not loaded');
      return null;
    }
    const { nodeColor3D, edgeStyle3D } = window.__viz.g3d.style;
    const { updateLabels } = window.__viz.g3d.labels;
    const { openDetail } = window.__viz.detail;
    const { escapeHtml } = window.__viz.util;

    const G = ForceGraph3D({ rendererConfig: { antialias: false, alpha: false, powerPreference: 'high-performance', stencil: false } })(container)
      .backgroundColor('#0e1116')
      .nodeId('id')
      .linkSource('source_id')
      .linkTarget('target_id')
      .nodeResolution(6)  // default 8. 6 segments halves tris per sphere (64→36), big win with 500 nodes.
      .nodeRelSize(7)
      .nodeVal(n => {
        if (state.selectedId === n.id) return 4;
        const s = _3dMaps.searchMatchIds;
        if (s && s.size && s.has(n.id)) return 3;
        var edges = Math.min(n._edgeCount || 0, 20);
        var content = Math.min((n._contentLen || 0) / 200, 5);
        return 1 + edges * 0.3 + content * 0.2;
      })
      .nodeColor(n => {
        if (state.selectedId === n.id) return '#ffffff';
        if (_3dMaps.hoveredId === n.id) return '#ffffff';
        const s = _3dMaps.searchMatchIds;
        if (s && s.size && s.has(n.id)) return '#ffffff';
        return nodeColor3D(n);
      })
      .nodeOpacity(0.95)
      .nodeLabel(n => {
        const parts = [
          '<div style="font-family:monospace;font-size:12px;padding:2px 4px;max-width:280px">',
          '<div style="font-weight:700">' + escapeHtml(n.title) + '</div>',
          '<div style="color:#b9c2cd;font-size:10.5px;margin-top:2px">',
          (n.project_name ? ('project=' + escapeHtml(n.project_name)) : ('scope=' + escapeHtml(n.scope))),
          ' · status=' + escapeHtml(n.status),
          (n.claim_type ? ' · ' + escapeHtml(n.claim_type) : ''),
          '</div></div>',
        ];
        return parts.join('');
      })
      .linkColor(l => edgeStyle3D(l.relation_type).color)
      .linkWidth(0)
      .linkOpacity(1)
      .linkDirectionalParticles(0)
      .cooldownTicks(60)
      .d3AlphaDecay(0.1)
      .d3VelocityDecay(0.5)
      .warmupTicks(0)
      .onEngineStop(() => {
        // Don't pauseAnimation — it kills OrbitControls interaction.
        // Physics already stopped (alpha < min). Renderer keeps running
        // for user drag/rotate, but at idle cost (~1 render/change event).
      })
      .onNodeHover(n => {
        const prev = _3dMaps.hoveredId;
        _3dMaps.hoveredId = n ? n.id : null;
        container.style.cursor = n ? 'pointer' : null;
        // Recolor only previous + current node materials (avoid full recompute)
        const recolorNode = window.__viz.g3d.lifecycle.recolorNode;
        if (prev != null && prev !== _3dMaps.hoveredId) recolorNode(prev);
        if (n) recolorNode(n.id);
      })
      .onNodeClick(n => {
        if (!n) return;
        // Share the DetailPanel with 2D + List views.
        openDetail(n.id);
        const dist = 80;
        const r = Math.hypot(n.x || 0, n.y || 0, n.z || 0) || 1;
        G.cameraPosition(
          { x: (n.x || 0) * (1 + dist/r), y: (n.y || 0) * (1 + dist/r), z: (n.z || 0) * (1 + dist/r) },
          n, 700
        );
      });

    // per-frame: reposition DOM labels from projected world→screen.
    G.onEngineTick(() => updateLabels(G));
    // OrbitControls fires 'change' at mousemove frequency (up to ~120/s on
    // high-rate mice). Collapse every burst into at most one rAF tick so
    // label updates never exceed display refresh rate. Also disable damping
    // so there are no trailing re-renders after the user stops dragging —
    // damping would otherwise fire 'change' events for ~500ms while the
    // camera eases to stop, forcing renders that nothing visually needs.
    try {
      const controls = G.controls && G.controls();
      if (controls) {
        if ('enableDamping' in controls) controls.enableDamping = false;
        if ('autoRotate' in controls) controls.autoRotate = false;
        if (controls.addEventListener) {
          let _ctrlRafPending = false;
          controls.addEventListener('change', () => {
            if (_ctrlRafPending) return;
            _ctrlRafPending = true;
            requestAnimationFrame(() => {
              _ctrlRafPending = false;
              // Re-render scene when user drags/rotates after engine stopped
              try {
                var renderer = G.renderer && G.renderer();
                var scene = G.scene && G.scene();
                var camera = G.camera && G.camera();
                if (renderer && scene && camera) renderer.render(scene, camera);
              } catch (_) {}
              updateLabels(G);
            });
          });
        }
      }
    } catch (_) { /* controls may not be ready */ }

    // Spread clusters more (matches viz_3d tuning).
    try {
      const charge = G.d3Force && G.d3Force('charge');
      if (charge && charge.strength) charge.strength(-140);
      const link = G.d3Force && G.d3Force('link');
      if (link && link.distance) link.distance(90);
    } catch (_) {}

    // Cap pixel ratio to 1 on Retina — 4x fewer pixels, massive GPU saving
    try {
      var renderer = G.renderer && G.renderer();
      if (renderer && renderer.setPixelRatio) renderer.setPixelRatio(1);
    } catch (_) {}

    return G;
  }

  // ---- 注册到 __viz 命名空间 ----
  window.__viz = window.__viz || {};
  window.__viz.g3d = window.__viz.g3d || {};
  window.__viz.g3d.initGraph = init3DGraph;
})();
