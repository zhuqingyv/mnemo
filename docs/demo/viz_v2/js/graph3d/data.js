/**
 * js/graph3d/data.js
 *
 * 职责：3D 图谱数据层 — 节点构造、边收集、首建、边数统计、增量 diff
 * 依赖：__viz.state（仅读 knowledge / _rawRelations）, __viz._3dMaps（nodes/links/索引）
 * 注册：__viz.g3d.data = { build3DGraphData, diff3DGraphData, recomputeEdgeCounts3D }
 *
 * 源行：docs/demo/viz_v2/index.html 2885–3036
 *
 * 注意：3D 的 nodes/links 存在 _3dMaps（非 reactive），3d-force-graph 才能安全
 *       读写节点上的 Float32Array 位置向量，不被 nova-dom Proxy 打断 receiver。
 */
(function () {
  'use strict';

  const state = window.__viz.state;
  const _3dMaps = window.__viz._3dMaps;

  function _newNode3D(k) {
    return {
      id: k.id,
      title: k.title || '(no title)',
      project_name: k.project_name || null,
      scope: k.scope || 'global',
      status: k.status || 'active',
      claim_type: k.claim_type || null,
      tags: k.tags || [],
      summary: k.summary || '',
      _contentLen: (k.summary || '').length + (k.content || '').length,
      _edgeCount: 0,
    };
  }

  // Collect the desired links set from state._rawRelations, filtering
  // against the current node id set and deduping by (min, max, type).
  function _collect3DLinks(idSet) {
    const seen = new Set();
    const out = [];
    for (const r of (state._rawRelations || [])) {
      if (!idSet.has(r.source_id) || !idSet.has(r.target_id)) continue;
      if (r.source_id === r.target_id) continue;
      const a = Math.min(r.source_id, r.target_id);
      const b = Math.max(r.source_id, r.target_id);
      const type = r.relation_type || 'related';
      const key = a + '-' + b + '-' + type;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({
        id: r.id,
        source_id: r.source_id,
        target_id: r.target_id,
        relation_type: type,
        weight: r.weight == null ? 1 : r.weight,
        _key: key,
      });
    }
    return out;
  }

  // Full build — used only on first activation. Populates the persistent
  // arrays held in _3dMaps so subsequent polls can diff them.
  function build3DGraphData() {
    _3dMaps.nodes.length = 0;
    _3dMaps.nodesById.clear();
    for (const k of state.knowledge) {
      const n = _newNode3D(k);
      _3dMaps.nodes.push(n);
      _3dMaps.nodesById.set(n.id, n);
    }
    const idSet = new Set(state.knowledge.map(k => k.id));
    const links = _collect3DLinks(idSet);
    _3dMaps.links.length = 0;
    for (const l of links) _3dMaps.links.push(l);
    recomputeEdgeCounts3D();
    return { nodes: _3dMaps.nodes, links: _3dMaps.links };
  }

  function recomputeEdgeCounts3D() {
    for (const n of _3dMaps.nodes) n._edgeCount = 0;
    for (const l of _3dMaps.links) {
      // 3d-force-graph may mutate source/target to refs after first tick;
      // read either shape.
      const sid = (typeof l.source === 'object' && l.source) ? l.source.id : l.source_id;
      const tid = (typeof l.target === 'object' && l.target) ? l.target.id : l.target_id;
      const s = _3dMaps.nodesById.get(sid);
      const t = _3dMaps.nodesById.get(tid);
      if (s) s._edgeCount++;
      if (t) t._edgeCount++;
    }
  }

  // Incremental diff — call on polling. Mutates the persistent arrays in
  // place (push / splice) so the simulation keeps existing nodes' x/y/z
  // instead of re-layouting from scratch.
  //
  // Returns {addedIds, removedIds, touched} so callers can update the
  // DOM label layer and recolor search matches without a full rebuild.
  function diff3DGraphData() {
    const knowledge = state.knowledge;
    const addedIds = [];
    const removedIds = [];
    let touched = false;

    const newIdSet = new Set();
    for (const k of knowledge) newIdSet.add(k.id);

    // Remove nodes that no longer exist.
    for (let i = _3dMaps.nodes.length - 1; i >= 0; i--) {
      const n = _3dMaps.nodes[i];
      if (!newIdSet.has(n.id)) {
        _3dMaps.nodes.splice(i, 1);
        _3dMaps.nodesById.delete(n.id);
        removedIds.push(n.id);
        touched = true;
      }
    }

    // Add new nodes; mutate existing nodes' shallow fields if changed.
    for (const k of knowledge) {
      const existing = _3dMaps.nodesById.get(k.id);
      if (!existing) {
        const n = _newNode3D(k);
        _3dMaps.nodes.push(n);
        _3dMaps.nodesById.set(n.id, n);
        addedIds.push(n.id);
        touched = true;
      } else {
        // Only rewrite flags that change colors / labels; keep x/y/z so
        // the simulation preserves layout.
        let changed = false;
        const nextStatus = k.status || 'active';
        if (existing.status !== nextStatus) { existing.status = nextStatus; changed = true; }
        const nextTitle = k.title || '(no title)';
        if (existing.title !== nextTitle) { existing.title = nextTitle; changed = true; }
        const nextProj = k.project_name || null;
        if (existing.project_name !== nextProj) { existing.project_name = nextProj; changed = true; }
        const nextScope = k.scope || 'global';
        if (existing.scope !== nextScope) { existing.scope = nextScope; changed = true; }
        if (changed) touched = true;
      }
    }

    // Diff links by a stable key. 3d-force-graph replaces source/target
    // with node refs after first tick; we keep our own source_id/target_id
    // fields untouched, and a _key fingerprint to diff against.
    const currentKeys = new Set();
    for (const l of _3dMaps.links) currentKeys.add(l._key);
    const desired = _collect3DLinks(newIdSet);
    const desiredKeys = new Set(desired.map(l => l._key));
    // Remove links not in desired set.
    for (let i = _3dMaps.links.length - 1; i >= 0; i--) {
      if (!desiredKeys.has(_3dMaps.links[i]._key)) {
        _3dMaps.links.splice(i, 1);
        touched = true;
      }
    }
    // Add links that are new.
    for (const l of desired) {
      if (!currentKeys.has(l._key)) {
        _3dMaps.links.push(l);
        touched = true;
      }
    }

    if (touched) recomputeEdgeCounts3D();
    return { addedIds, removedIds, touched };
  }

  // ---- 注册到 __viz 命名空间 ----
  window.__viz = window.__viz || {};
  window.__viz.g3d = window.__viz.g3d || {};
  window.__viz.g3d.data = {
    build3DGraphData,
    diff3DGraphData,
    recomputeEdgeCounts3D,
  };
})();
