/**
 * js/data-loaders.js
 *
 * 职责：HTTP 数据加载 — probeHealth / loadStats / loadKnowledge / loadRelations
 * 依赖：__viz.state, __viz.apiGet, __viz.API, __viz.HEALTH
 * 注册：__viz.loader = { probeHealth, loadStats, loadKnowledge, loadRelations }
 */
(function () {
  'use strict';

  const state = window.__viz.state;
  const { apiGet, API, HEALTH } = window.__viz;

  async function loadStats() {
    try {
      state.stats = await apiGet(API + '/stats');
    } catch (e) {
      console.warn('stats load failed', e);
    }
  }

  async function loadKnowledge() {
    try {
      const data = await apiGet(API + '/knowledge?limit=500');
      state.knowledge = data.results || [];
      state.loaded = true;
    } catch (e) {
      console.warn('knowledge load failed', e);
    }
  }

  async function loadRelations() {
    try {
      const data = await apiGet(API + '/relations?limit=5000');
      state._rawRelations = data.results || [];
    } catch (e) {
      console.warn('relations load failed', e);
    }
  }

  async function probeHealth() {
    try {
      const data = await apiGet(HEALTH);
      state.serverOnline = true;
      state.mnemoVersion = data.version || null;
    } catch (e) {
      state.serverOnline = false;
    }
  }

  // ---- 注册到 __viz ----
  window.__viz.loader = {
    probeHealth,
    loadStats,
    loadKnowledge,
    loadRelations,
  };
})();
