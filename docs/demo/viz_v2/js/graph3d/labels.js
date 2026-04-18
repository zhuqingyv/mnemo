/**
 * js/graph3d/labels.js
 *
 * 职责：3D 图谱 DOM 标签层 — 首建 500 div、增量 add/remove、每帧 world→screen 投影
 * 依赖：__viz._3dMaps（nodes/labelDistance/labelEls/nodesById）, __viz.g3d.style.truncTitle3D, 全局 THREE
 * 注册：__viz.g3d.labels = { rebuildLabels, updateLabelLayer, updateLabels }
 *
 * 源行：docs/demo/viz_v2/index.html 3041–3155
 */
(function () {
  'use strict';

  const _3dMaps = window.__viz._3dMaps;
  const { truncTitle3D } = window.__viz.g3d.style;

  // Full rebuild — used only on first 3D activation. 500 divs are cheap
  // to create once; the per-frame cost is already dominated by transform
  // updates on the visible subset, not by DOM count.
  function rebuild3DLabels(nodes) {
    const layer = document.getElementById('label-layer');
    if (!layer) return;
    layer.innerHTML = '';
    _3dMaps.labelEls = new Map();
    const frag = document.createDocumentFragment();
    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      const el = document.createElement('div');
      el.className = 'n-label hide';
      el._hidden = true;
      el.textContent = truncTitle3D(n.title);
      frag.appendChild(el);
      _3dMaps.labelEls.set(n.id, el);
    }
    layer.appendChild(frag);
  }

  // Incremental label update — called after diff3DGraphData. Only touches
  // DOM for added/removed ids; existing nodes keep their label div.
  function updateLabelLayer3D(addedIds, removedIds) {
    const layer = document.getElementById('label-layer');
    if (!layer) return;
    const labels = _3dMaps.labelEls;
    // Remove labels for deleted nodes.
    for (let i = 0; i < removedIds.length; i++) {
      const id = removedIds[i];
      const el = labels.get(id);
      if (el && el.parentNode) el.parentNode.removeChild(el);
      labels.delete(id);
    }
    // Add labels for new nodes.
    if (addedIds.length) {
      const frag = document.createDocumentFragment();
      const idx = _3dMaps.nodesById;
      for (let i = 0; i < addedIds.length; i++) {
        const id = addedIds[i];
        const n = idx.get(id);
        if (!n) continue;
        const el = document.createElement('div');
        el.className = 'n-label hide';
        el._hidden = true;
        el.textContent = truncTitle3D(n.title);
        frag.appendChild(el);
        labels.set(id, el);
      }
      layer.appendChild(frag);
    }
  }

  // reused scratch vector for projection (avoid per-node per-frame alloc)
  let _scratchVec3D = null;
  // Cached layer ref + dims. Reading clientWidth/Height per tick forces a
  // style+layout recalc ("forced reflow") which spikes main-thread cost on
  // every orbit mousemove. We refresh these only when the layer resizes.
  let _cachedLayer = null;
  let _cachedW = 0, _cachedH = 0;
  function _refreshLayerDims() {
    if (!_cachedLayer) _cachedLayer = document.getElementById('label-layer');
    if (!_cachedLayer) return false;
    _cachedW = _cachedLayer.clientWidth;
    _cachedH = _cachedLayer.clientHeight;
    return _cachedW > 0 && _cachedH > 0;
  }
  // Hook ResizeObserver once on first call so cached dims track container
  // changes (panel open/close, window resize) without per-frame reads.
  let _resizeObserverBound = false;
  function _ensureResizeObserver() {
    if (_resizeObserverBound) return;
    if (typeof ResizeObserver === 'undefined') return;
    const layer = _cachedLayer || document.getElementById('label-layer');
    if (!layer) return;
    new ResizeObserver(() => {
      _cachedW = layer.clientWidth;
      _cachedH = layer.clientHeight;
    }).observe(layer);
    _resizeObserverBound = true;
  }

  // Hot path — runs once per engine tick (up to 60 Hz during simulation)
  // and on every controls 'change' (orbit drag). Optimizations:
  //   - classical for loop (v8 optimizes better than for-of in hot loops)
  //   - _scratchVec3D reused across calls (no alloc)
  //   - el._hidden boolean caches hide state; avoids classList churn
  //     which would pay string compare + DOMTokenList work every node
  //   - iterate the persistent _3dMaps.nodes (same ref the engine
  //     owns) instead of calling G.graphData() which does property lookups
  //   - hoist all state reads outside the loop
  function updateLabels3D(G) {
    if (!G) return;
    const labels = _3dMaps.labelEls;
    if (!labels || labels.size === 0) return;
    const cam = G.camera && G.camera();
    if (!cam) return;
    // First call: seed the cache (reads clientWidth/Height exactly once);
    // afterwards ResizeObserver keeps _cachedW/H fresh without a per-tick read.
    if (!_cachedW || !_cachedH) {
      if (!_refreshLayerDims()) return;
      _ensureResizeObserver();
    }
    const w = _cachedW;
    const h = _cachedH;
    const halfW = w * 0.5, halfH = h * 0.5;
    const threshold = _3dMaps.labelDistance;
    const threshold2 = threshold * threshold;  // avoid sqrt per node
    const camPos = cam.position;
    const cpx = camPos.x, cpy = camPos.y, cpz = camPos.z;
    if (!_scratchVec3D && typeof THREE !== 'undefined') _scratchVec3D = new THREE.Vector3();
    const v = _scratchVec3D || new THREE.Vector3();
    cam.updateMatrixWorld();

    const nodes = _3dMaps.nodes;
    const len = nodes.length;
    for (let i = 0; i < len; i++) {
      const n = nodes[i];
      const el = labels.get(n.id);
      if (!el) continue;
      const nx = n.x, ny = n.y, nz = n.z;
      if (nx == null || ny == null || nz == null) {
        if (!el._hidden) { el.className = 'n-label hide'; el._hidden = true; }
        continue;
      }
      const dx = nx - cpx, dy = ny - cpy, dz = nz - cpz;
      const d2 = dx*dx + dy*dy + dz*dz;
      if (d2 > threshold2) {
        if (!el._hidden) { el.className = 'n-label hide'; el._hidden = true; }
        continue;
      }
      v.set(nx, ny, nz).project(cam);
      const vz = v.z;
      if (vz < -1 || vz > 1) {
        if (!el._hidden) { el.className = 'n-label hide'; el._hidden = true; }
        continue;
      }
      const sx = v.x * halfW + halfW;
      const sy = -v.y * halfH + halfH;
      if (sx < -40 || sx > w + 40 || sy < -20 || sy > h + 20) {
        if (!el._hidden) { el.className = 'n-label hide'; el._hidden = true; }
        continue;
      }
      if (el._hidden) { el.className = 'n-label'; el._hidden = false; }
      el.style.transform = 'translate3d(' + (sx|0) + 'px,' + ((sy - 10)|0) + 'px,0) translate(-50%, -100%)';
    }
  }

  // ---- 注册到 __viz 命名空间 ----
  window.__viz = window.__viz || {};
  window.__viz.g3d = window.__viz.g3d || {};
  window.__viz.g3d.labels = {
    rebuildLabels: rebuild3DLabels,
    updateLabelLayer: updateLabelLayer3D,
    updateLabels: updateLabels3D,
  };
})();
