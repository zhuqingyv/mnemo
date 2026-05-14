/**
 * js/graph3d/style.js
 *
 * 职责：3D 图谱样式 — 项目分色、节点色、边样式、标签截断（纯函数层）
 * 依赖：无（不读 __viz.*，不触 DOM / three.js）
 * 注册：__viz.g3d.style = { projectColor3D, nodeColor3D, edgeStyle3D, truncTitle3D }
 *
 * 源行：viz/viz_v2/index.html 2848–2883
 */
(function () {
  'use strict';

  // --- 3D color mapping (distinct cache from 2D's scope-only coloring) ---
  const PROJECT_HUES_3D = [210, 140, 30, 280, 350, 170, 60, 310, 100, 240, 0, 190, 330, 80, 260];
  const _projectColorCache3D = {};
  let _projectColorIdx3D = 0;
  function projectColor3D(name) {
    if (!name) return null;
    if (_projectColorCache3D[name]) return _projectColorCache3D[name];
    const hue = PROJECT_HUES_3D[_projectColorIdx3D % PROJECT_HUES_3D.length];
    _projectColorIdx3D++;
    _projectColorCache3D[name] = 'hsl(' + hue + ', 65%, 55%)';
    return _projectColorCache3D[name];
  }
  function nodeColor3D(n) {
    const status = n.status || 'active';
    if (status === 'stale') return '#d29922';
    if (status === 'superseded' || status === 'archived') return '#6e7681';
    const pc = projectColor3D(n.project_name);
    if (pc) return pc;
    const scope = n.scope || 'global';
    if (scope === 'global') return '#58a6ff';
    if (scope === 'session') return '#6e7681';
    return '#3fb950';
  }
  const EDGE_STYLES_3D = {
    related:      { color: 'rgba(88,166,255,0.35)', width: 0.6 },
    auto_related: { color: 'rgba(210,153,34,0.38)', width: 0.5 },
    supersedes:   { color: '#a371f7',                width: 1.2 },
    contradicts:  { color: '#f85149',                width: 2.2 },
  };
  function edgeStyle3D(t) { return EDGE_STYLES_3D[t] || EDGE_STYLES_3D.related; }

  function truncTitle3D(t) {
    if (!t) return '';
    if (t.length <= 20) return t;
    return t.slice(0, 18) + '…';
  }

  // ---- 注册到 __viz 命名空间 ----
  window.__viz = window.__viz || {};
  window.__viz.g3d = window.__viz.g3d || {};
  window.__viz.g3d.style = {
    projectColor3D,
    nodeColor3D,
    edgeStyle3D,
    truncTitle3D,
  };
})();
