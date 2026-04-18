/**
 * js/graph2d/physics.js — Barnes-Hut O(n log n) 斥力 + 弹簧/阻尼/向心/冻结 + 分级 markPhysicsActive
 * 注册：__viz.g2d.physics.{stepPhysics, markPhysicsActive}
 * 优化：QuadTree 对象池消除每帧 new；markPhysicsActive(reason) 分级（hover/pan=0，drag=0.3，struct=1.0）。
 */
(function () {
  'use strict';

  const state = window.__viz.state;
  const _graphMaps = window.__viz._graphMaps;
  const ALPHA_RESTART = window.__viz.const.ALPHA_RESTART;

  // VIZ_SMART_PAUSE 是模块内同值常量（URL ?smartPause=0|false 关闭）
  const VIZ_SMART_PAUSE = (() => {
    try {
      const p = new URLSearchParams(window.location.search).get('smartPause');
      if (p === '0' || p === 'false') return false;
    } catch (_) {}
    return true;
  })();

  // reason: 'struct' (add/remove node/edge) → 1.0
  //         'drag'                          → 0.3 (轻加温，避免 4s CPU 尾巴)
  //         'hover' / 'pan'                 → 0   (不扰动物理)
  //         undefined                       → 1.0 (向后兼容旧调用)
  function markPhysicsActive(reason) {
    const g = state.graph;
    let bump;
    if (reason === 'hover' || reason === 'pan') bump = 0;
    else if (reason === 'drag') bump = 0.7;
    else bump = ALPHA_RESTART;
    if (bump > 0) {
      if (bump > g.alpha) g.alpha = bump;
      g.physicsActive = true;
      // Unfreeze all nodes so they respond to the new energy
      var rn = _graphMaps.rawNodes;
      for (var i = 0; i < rn.length; i++) { rn[i]._frozen = false; rn[i]._slowFrames = 0; }
    }
    g.renderDirty = true;
    window.__viz.g2d.loop.ensureRAF();
  }

  // ---- Barnes-Hut QuadTree with object pool ----
  function QuadTree(x, y, w, h) {
    this.reset(x, y, w, h);
  }
  QuadTree.prototype.reset = function (x, y, w, h) {
    this.x = x; this.y = y; this.w = w; this.h = h;
    this.body = null;
    this.mass = 0;
    this.cx = 0;
    this.cy = 0;
    this.children = null;
    return this;
  };
  QuadTree.prototype.insert = function (node) {
    if (this.mass === 0) {
      this.body = node; this.mass = 1;
      this.cx = node.x; this.cy = node.y;
      return;
    }
    if (this.children === null) {
      this._subdivide();
      this._insertIntoChild(this.body);
      this.body = null;
    }
    this._insertIntoChild(node);
    var newMass = this.mass + 1;
    this.cx = (this.cx * this.mass + node.x) / newMass;
    this.cy = (this.cy * this.mass + node.y) / newMass;
    this.mass = newMass;
  };
  QuadTree.prototype._subdivide = function () {
    var hw = this.w / 2, hh = this.h / 2;
    this.children = [
      _alloc(this.x,      this.y,      hw, hh),
      _alloc(this.x + hw, this.y,      hw, hh),
      _alloc(this.x,      this.y + hh, hw, hh),
      _alloc(this.x + hw, this.y + hh, hw, hh),
    ];
  };
  QuadTree.prototype._insertIntoChild = function (node) {
    var mx = this.x + this.w / 2, my = this.y + this.h / 2;
    var idx = (node.x >= mx ? 1 : 0) + (node.y >= my ? 2 : 0);
    this.children[idx].insert(node);
  };
  QuadTree.prototype.applyForce = function (node, KR, theta) {
    if (this.mass === 0) return;
    var dx = node.x - this.cx;
    var dy = node.y - this.cy;
    var d2 = dx * dx + dy * dy;
    if (d2 < 0.01) { dx = (Math.random() - 0.5) * 0.1; dy = (Math.random() - 0.5) * 0.1; d2 = dx * dx + dy * dy + 0.01; }
    if (this.children === null) {
      if (this.body === node) return;
      var d = Math.sqrt(d2);
      var f = KR / d2;
      node.vx += (dx / d) * f;
      node.vy += (dy / d) * f;
      return;
    }
    if (this.w / Math.sqrt(d2) < theta) {
      var d2s = Math.sqrt(d2);
      var f2 = KR * this.mass / d2;
      node.vx += (dx / d2s) * f2;
      node.vy += (dy / d2s) * f2;
      return;
    }
    for (var i = 0; i < 4; i++) this.children[i].applyForce(node, KR, theta);
  };

  // ---- Pool: 单独分配（内部节点的 4 个孩子也复用同一池） ----
  var _pool = [];
  var _poolIdx = 0;
  function _alloc(x, y, w, h) {
    if (_poolIdx < _pool.length) return _pool[_poolIdx++].reset(x, y, w, h);
    var t = new QuadTree(x, y, w, h);
    _pool.push(t);
    _poolIdx++;
    return t;
  }

  function stepPhysics() {
    const g = state.graph;
    // iterate raw (non-reactive) mirrors to avoid Proxy trap on every read
    const nodes = _graphMaps.rawNodes;
    const edges = _graphMaps.rawEdges;
    if (!nodes.length) return;
    const c = document.getElementById('graph-canvas');
    if (!c) return;
    const cx = c.clientWidth / 2;
    const cy = c.clientHeight / 2;

    const aScale = VIZ_SMART_PAUSE ? g.alpha : 1.0;

    const KR = 5000 * aScale;
    const THETA = 0.8;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      if (n.x < minX) minX = n.x;
      if (n.y < minY) minY = n.y;
      if (n.x > maxX) maxX = n.x;
      if (n.y > maxY) maxY = n.y;
    }
    const pad = 10;
    const qw = Math.max(maxX - minX + pad, maxY - minY + pad, 100);
    _poolIdx = 0; // reset pool cursor per frame
    const tree = _alloc(minX - pad / 2, minY - pad / 2, qw, qw);
    for (let i = 0; i < nodes.length; i++) tree.insert(nodes[i]);

    const dragNode = g.drag && g.drag.node;
    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      if (n.fixed && n !== dragNode) continue;
      if (n._frozen && n !== dragNode) continue;
      tree.applyForce(n, KR, THETA);
    }
    const SPRING = 0.004 * aScale;
    const REST = 140;
    for (const e of edges) {
      const a = _graphMaps.nodeIndex.get(e.s);
      const b = _graphMaps.nodeIndex.get(e.t);
      if (!a || !b) continue;
      if (a._frozen && b._frozen) continue;
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const delta = d - REST;
      const fx = (dx / d) * delta * SPRING;
      const fy = (dy / d) * delta * SPRING;
      a.vx += fx; a.vy += fy;
      b.vx -= fx; b.vy -= fy;
    }
    const DAMP = 0.6;
    const CENTER = 0.0015 * aScale;
    const vmax = 10;
    for (const n of nodes) {
      if (n.fixed) { n.vx = 0; n.vy = 0; continue; }
      if (n._frozen && n !== dragNode) { n.vx = 0; n.vy = 0; continue; }
      n.vx += (cx - n.x) * CENTER;
      n.vy += (cy - n.y) * CENTER;
      n.vx *= DAMP;
      n.vy *= DAMP;
      if (n.vx > vmax) n.vx = vmax; else if (n.vx < -vmax) n.vx = -vmax;
      if (n.vy > vmax) n.vy = vmax; else if (n.vy < -vmax) n.vy = -vmax;
      n.x += n.vx;
      n.y += n.vy;
      const speed2 = n.vx * n.vx + n.vy * n.vy;
      if (speed2 < 0.1) n._slowFrames = (n._slowFrames || 0) + 1;
      else n._slowFrames = 0;
      if (n._slowFrames > 30) n._frozen = true;
    }
  }

  // ---- 注册到 __viz 命名空间 ----
  window.__viz.g2d = window.__viz.g2d || {};
  window.__viz.g2d.physics = { stepPhysics, markPhysicsActive };
})();
