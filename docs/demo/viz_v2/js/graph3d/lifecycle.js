/**
 * js/graph3d/lifecycle.js
 *
 * 职责：3D 图谱生命周期 — 单节点重染色 / 搜索匹配同步 / 激活 / 停用
 * 依赖：__viz.state, __viz._3dMaps, __viz.g3d.initGraph, __viz.g3d.style.nodeColor3D,
 *       __viz.g3d.data.build3DGraphData, __viz.g3d.labels.{rebuildLabels,updateLabels},
 *       __viz.g2d.loop.stopGraphLoop
 * 注册：__viz.g3d.lifecycle = { recolorNode, updateSearchMatch, activate, deactivate }
 *
 * 源行：docs/demo/viz_v2/index.html 3253–3348
 */
(function () {
  'use strict';

  const state = window.__viz.state;
  const _3dMaps = window.__viz._3dMaps;

  // Lazy CDN loader: injected on first 3D activation so users who stay in
  // 2D / list never download ~900KB of three + 3d-force-graph.
  const CDN_SCRIPTS = [
    'https://unpkg.com/three@0.160.0/build/three.min.js',
    'https://unpkg.com/3d-force-graph@1.73.4/dist/3d-force-graph.min.js',
  ];
  let _libsPromise = null;
  function _loadScript(src) {
    return new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = src; s.async = false; // preserve order: THREE must load before 3d-force-graph
      s.onload = resolve;
      s.onerror = () => reject(new Error('failed to load ' + src));
      document.head.appendChild(s);
    });
  }
  function _ensure3DLibs() {
    if (typeof ForceGraph3D !== 'undefined' && typeof THREE !== 'undefined') {
      return Promise.resolve();
    }
    if (_libsPromise) return _libsPromise;
    _libsPromise = CDN_SCRIPTS.reduce(
      (p, src) => p.then(() => _loadScript(src)),
      Promise.resolve(),
    );
    // If either CDN request failed, drop the cached promise so a later 3D
    // activation gets a fresh retry instead of being permanently stuck.
    _libsPromise.catch(() => { _libsPromise = null; });
    return _libsPromise;
  }
  // Surface a load failure inside the 3D container.
  function _showLoadError() {
    const container = document.getElementById('graph-3d-container');
    if (!container) return;
    if (container.querySelector('.graph3d-load-error')) return;
    const msg = document.createElement('div');
    msg.className = 'graph3d-load-error';
    msg.style.cssText =
      'position:absolute;inset:0;display:flex;align-items:center;justify-content:center;' +
      'color:#b9c2cd;font:12px/1.6 monospace;text-align:center;padding:24px;pointer-events:none;';
    msg.textContent = '3D 依赖加载失败（three.js / 3d-force-graph）。请检查网络后再次切到 3D 重试。';
    if (!container.style.position) container.style.position = 'relative';
    container.appendChild(msg);
  }
  function _clearLoadError() {
    const container = document.getElementById('graph-3d-container');
    if (!container) return;
    const e = container.querySelector('.graph3d-load-error');
    if (e) e.remove();
  }

  // Recolor a single node's material without walking the whole graph.
  function _recolorNode3D(nodeId) {
    const { nodeColor3D } = window.__viz.g3d.style;
    const node = _3dMaps.nodesById.get(nodeId);
    if (!node) return;
    const obj = node.__threeObj;
    if (!obj) return;
    const target = (obj.material ? obj : (obj.children && obj.children.find(c => c && c.material && c.material.color)));
    if (!target || !target.material || !target.material.color) return;
    let c;
    if (state.selectedId === nodeId) c = '#ffffff';
    else if (_3dMaps.hoveredId === nodeId) c = '#ffffff';
    else if (_3dMaps.searchMatchIds.size && _3dMaps.searchMatchIds.has(nodeId)) c = '#ffffff';
    else c = nodeColor3D(node);
    target.material.color.set(c);
  }

  // Recolor only nodes entering/exiting the search match set, fly to first.
  function update3DSearchMatch() {
    if (!_3dMaps.G) return;
    const prev = _3dMaps.searchMatchIds;
    const next = new Set();
    if (state.searchResults && state.searchResults.length) {
      for (const r of state.searchResults) next.add(r.id);
    }
    _3dMaps.searchMatchIds = next;

    // Diff: recolor only entered + exited nodes.
    for (const id of prev) {
      if (!next.has(id)) _recolorNode3D(id);
    }
    for (const id of next) {
      if (!prev.has(id)) _recolorNode3D(id);
    }

    // Fly to first match (only when search is active).
    if (next.size > 0 && state.searchResults && state.searchResults.length) {
      const first = state.searchResults[0];
      if (first) {
        const G = _3dMaps.G;
        const node = _3dMaps.nodesById.get(first.id);
        if (node && node.x !== undefined) {
          const r = Math.hypot(node.x, node.y, node.z) || 1;
          const dist = 140;
          G.cameraPosition(
            { x: node.x * (1 + dist/r), y: node.y * (1 + dist/r), z: node.z * (1 + dist/r) },
            node, 700
          );
          // camera tween animates via internal rAF; engine itself can stay paused.
        }
      }
    }
  }

  function activate3DGraph() {
    // Stop the 2D RAF loop first — mutual exclusion.
    const stopGraphLoop = window.__viz.g2d && window.__viz.g2d.loop && window.__viz.g2d.loop.stopGraphLoop;
    if (typeof stopGraphLoop === 'function') stopGraphLoop();
    if (_3dMaps.G) return;  // already built (re-entry guard)
    _clearLoadError();
    _ensure3DLibs().then(async () => {
      if (state.view !== '3d' || _3dMaps.G) return;
      if (!Array.isArray(state._rawRelations)) {
        await window.__viz.loader.loadRelations();
      }
      if (state.view !== '3d' || _3dMaps.G) return;
      const init3DGraph = window.__viz.g3d.initGraph;
      const { build3DGraphData } = window.__viz.g3d.data;
      const { rebuildLabels } = window.__viz.g3d.labels;
      const G = init3DGraph();
      if (!G) return;
      _3dMaps.G = G;
      const data = build3DGraphData();
      G.graphData(data);
      rebuildLabels(data.nodes);
      _3dMaps.labelDistance = Math.max(80, Math.min(260, 60 + data.nodes.length * 0.25));
    }).catch((e) => {
      console.warn('[viz] 3D libs failed to load', e);
      _showLoadError();
    });
  }

  // Deep teardown: release the entire 3D graph when the user switches
  // away. Keeping `_3dMaps.G` alive between views holds ~5000 buffer
  // geometries in VRAM + 500 DOM label divs + a canvas listening for
  // controls 'change' events. CDP evidence showed the cheap pause-only
  // path left `renderer.info.memory.geometries = 5086` while invisible.
  // Next activation rebuilds via the existing lazy-init branch (~1s).
  function deactivate3DGraph() {
    const G = _3dMaps.G;
    if (G) {
      try { if (G.pauseAnimation) G.pauseAnimation(); } catch (_) {}
      // Drop all node/link Object3Ds from the scene. The library disposes
      // per-node geometries/materials here via its internal dispose events.
      try { G.graphData({ nodes: [], links: [] }); } catch (_) {}
      // Release WebGL resources (programs, buffers, textures).
      try {
        const r = G.renderer && G.renderer();
        if (r) {
          if (r.dispose) r.dispose();
          if (r.forceContextLoss) r.forceContextLoss();
          if (r.domElement && r.domElement.parentNode) {
            r.domElement.parentNode.removeChild(r.domElement);
          }
        }
      } catch (_) {}
      // Best-effort library-level destructor (public method exists in
      // three-forcegraph-derived graphs; guarded because it may not).
      try { if (G._destructor) G._destructor(); } catch (_) {}
      _3dMaps.G = null;
    }
    // Reset cached maps so the next activate rebuilds cleanly.
    _3dMaps.nodes.length = 0;
    _3dMaps.links.length = 0;
    _3dMaps.nodesById.clear();
    _3dMaps.hoveredId = null;
    _3dMaps.searchMatchIds = new Set();
    // Clear label-layer DOM so there are no stale divs competing for
    // layout while 2D/list views are active.
    const layer = document.getElementById('label-layer');
    if (layer) layer.innerHTML = '';
    if (_3dMaps.labelEls && _3dMaps.labelEls.clear) _3dMaps.labelEls.clear();
  }

  // ---- 注册到 __viz 命名空间 ----
  window.__viz = window.__viz || {};
  window.__viz.g3d = window.__viz.g3d || {};
  window.__viz.g3d.lifecycle = {
    recolorNode: _recolorNode3D,
    updateSearchMatch: update3DSearchMatch,
    activate: activate3DGraph,
    deactivate: deactivate3DGraph,
  };
})();
