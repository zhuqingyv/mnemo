/** graph2d/loop.js — rAF 主循环 + alpha 衰减 + 动画步进 + 60fps 帧率限制 */
/*
 * 优化：hasLiveAnimations 走 O(1) — stepAnimations 维护 _liveCount，pulses 用 length。
 */
(function () {
  'use strict';

  const state = window.__viz.state;
  const _graphMaps = window.__viz._graphMaps;
  const {
    ALPHA_DECAY, ALPHA_MIN,
    FADE_IN_MS, FADE_OUT_MS, EDGE_FADE_IN_MS, COLOR_TWEEN_MS,
    HALO_DURATION_MS, PULSE_DURATION_MS,
  } = window.__viz.const;

  // live-animation counter, maintained by stepAnimations
  let _liveCount = 0;

  const VIZ_SMART_PAUSE = (() => {
    try {
      const p = new URLSearchParams(window.location.search).get('smartPause');
      if (p === '0' || p === 'false') return false;
    } catch (_) {}
    return true;
  })();

  function markRenderDirty() { state.graph.renderDirty = true; ensureRAF(); }

  function hasLiveAnimations() {
    return _liveCount > 0 || state.graph.pulses.length > 0;
  }

  function ensureRAF() {
    if (state.graph.rafId || !state.graph.built) return;
    state.graph.rafId = requestAnimationFrame(graphTick);
  }

  const __vizPerf = {
    frameTimes: [],
    _lastActiveAt: performance.now(),
    framesLast5s() {
      const now = performance.now();
      return this.frameTimes.filter(t => now - t < 5000).length;
    },
    idleStateSince() { return performance.now() - this._lastActiveAt; },
  };
  window.__vizPerf = __vizPerf;

  var _lastFrameTime = 0;
  var TARGET_FRAME_MS = 1000 / 60; // lock to 60fps max

  function graphTick() {
    const g = state.graph;
    let needsNextFrame = false;
    const frameStart = performance.now();
    if (frameStart - _lastFrameTime < TARGET_FRAME_MS - 1) {
      g.rafId = requestAnimationFrame(graphTick);
      return;
    }
    _lastFrameTime = frameStart;
    __vizPerf.frameTimes.push(frameStart);
    if (__vizPerf.frameTimes.length > 700) __vizPerf.frameTimes.splice(0, 200);

    if (!VIZ_SMART_PAUSE || g.physicsActive) {
      window.__viz.g2d.physics.stepPhysics();
      if (VIZ_SMART_PAUSE) {
        g.alpha *= ALPHA_DECAY;
        if (g.alpha < ALPHA_MIN) g.physicsActive = false;
      }
      g.renderDirty = true;
    }

    stepAnimations();

    const animAlive = hasLiveAnimations();
    if (animAlive || g.camDirty || g.hoverDirty) g.renderDirty = true;

    if (g.renderDirty) {
      window.__viz.g2d.render.drawGraph();
      g.renderDirty = false;
      g.camDirty = false;
      g.hoverDirty = false;
    }

    if (!VIZ_SMART_PAUSE) needsNextFrame = true;
    else needsNextFrame = g.physicsActive || animAlive || g.drag != null;

    if (needsNextFrame) {
      g.rafId = requestAnimationFrame(graphTick);
      __vizPerf._lastActiveAt = frameStart;
    } else {
      g.rafId = null;
    }
  }

  function startGraphLoop() {
    if (state.graph.rafId) return;
    state.graph.renderDirty = true;
    state.graph.rafId = requestAnimationFrame(graphTick);
  }
  function stopGraphLoop() {
    if (state.graph.rafId) {
      cancelAnimationFrame(state.graph.rafId);
      state.graph.rafId = null;
    }
    state.graph.physicsActive = false;
  }

  // 给每个"活着"的动画状态打上 _liveAnim 标记，确保加减次数一致（幂等）。
  function _markLive(obj) { if (!obj._liveAnim) { obj._liveAnim = true; _liveCount++; } }
  function _clearLive(obj) { if (obj._liveAnim) { obj._liveAnim = false; _liveCount--; } }

  function stepAnimations() {
    const g = state.graph;
    const now = performance.now();
    const { lerpColor } = window.__viz.g2d.color;
    const rawNodes = _graphMaps.rawNodes;
    const rawEdges = _graphMaps.rawEdges;

    for (let i = rawNodes.length - 1; i >= 0; i--) {
      const n = rawNodes[i];
      if (n.removing) {
        const t = (now - n.removedAt) / FADE_OUT_MS;
        n.opacity = Math.max(0, 1 - t);
        if (t >= 1) {
          _clearLive(n);
          rawNodes.splice(i, 1);
          // also splice the reactive mirror so watchers see the change
          const ri = g.nodes.indexOf(n);
          if (ri >= 0) g.nodes.splice(ri, 1);
          _graphMaps.nodeIndex.delete(n.id);
          continue;
        }
        _markLive(n);
      } else {
        const fading = n.opacity < 1 && n.enteredAt && (now - n.enteredAt) < FADE_IN_MS;
        if (fading) {
          const t = (now - n.enteredAt) / FADE_IN_MS;
          n.opacity = Math.min(1, Math.max(0, t));
        }
        const halo = n.enteredAt && (now - n.enteredAt) < HALO_DURATION_MS;
        const tweening = !!(n.fillFromColor && n.fillStartAt);
        if (tweening) {
          const t = (now - n.fillStartAt) / COLOR_TWEEN_MS;
          if (t >= 1) {
            n.fill = n.targetFill;
            n.fillFromColor = null;
            n.fillStartAt = 0;
          } else {
            n.fill = lerpColor(n.fillFromColor, n.targetFill, t);
          }
        }
        const stillTweening = !!(n.fillFromColor && n.fillStartAt);
        if (fading || halo || stillTweening) _markLive(n);
        else _clearLive(n);
      }
    }

    for (let i = rawEdges.length - 1; i >= 0; i--) {
      const e = rawEdges[i];
      if (e.removing) {
        const t = (now - e.removedAt) / FADE_OUT_MS;
        e.opacity = Math.max(0, 1 - t);
        if (t >= 1) {
          _clearLive(e);
          rawEdges.splice(i, 1);
          const ri = g.edges.indexOf(e);
          if (ri >= 0) g.edges.splice(ri, 1);
          _graphMaps.edgeIndex.delete(e.s + '-' + e.t);
          continue;
        }
        _markLive(e);
      } else {
        const fading = e.opacity < 1 && e.enteredAt && (now - e.enteredAt) < EDGE_FADE_IN_MS;
        if (fading) {
          const t = (now - e.enteredAt) / EDGE_FADE_IN_MS;
          e.opacity = Math.min(1, Math.max(0, t));
          _markLive(e);
        } else _clearLive(e);
      }
    }

    const pulses = g.pulses;
    for (let i = pulses.length - 1; i >= 0; i--) {
      if (now - pulses[i].startedAt >= PULSE_DURATION_MS) pulses.splice(i, 1);
    }
  }

  // ---- 注册到 __viz 命名空间 ----
  window.__viz.g2d = window.__viz.g2d || {};
  window.__viz.g2d.loop = {
    markRenderDirty, ensureRAF, startGraphLoop, stopGraphLoop,
    hasLiveAnimations, graphTick, stepAnimations,
  };
})();
