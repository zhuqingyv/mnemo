/**
 * js/polling.js
 *
 * 职责：setInterval 轮询调度 — 按当前 view 分派到 graph diff 或 loader 全量拉取
 * 依赖：__viz.state, __viz.g2d.diff.refreshGraphIncremental, __viz.g2d.pulses.pollSearchPulses, __viz.loader.{loadStats,loadKnowledge,loadRelations}
 * 注册：__viz.polling = { startPolling, stopPolling }
 */
(function () {
  'use strict';

  const state = window.__viz.state;

  function startPolling() {
    if (state.pollTimer) return;
    state.pollTimer = setInterval(() => {
      const { refreshGraphIncremental } = window.__viz.g2d.diff;
      const { pollSearchPulses } = window.__viz.g2d.pulses;
      const { loadStats, loadKnowledge, loadRelations } = window.__viz.loader;

      const _3dMaps = window.__viz._3dMaps;
      if (state.view === '2d' && state.graph.built) {
        refreshGraphIncremental();
        pollSearchPulses();
      } else if (state.view === '3d' && _3dMaps && _3dMaps.G) {
        refreshGraphIncremental();
        pollSearchPulses();
      } else {
        loadStats();
        loadKnowledge();
        loadRelations();
      }
    }, state.pollIntervalMs);
  }

  function stopPolling() {
    if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; }
  }

  // ---- 注册到 __viz ----
  window.__viz.polling = {
    startPolling,
    stopPolling,
  };
})();
