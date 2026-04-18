/**
 * js/graph2d/pulses.js
 *
 * 职责：实时 search-pulse 轮询 — 从 /events/recent 拉取，解析 (id: N) 生成节点脉冲
 * 依赖：__viz.state, __viz._graphMaps, __viz._pulseSeenEventIds,
 *       __viz.const.{PULSE_WINDOW_SECONDS, PULSE_SEEN_CAP}, __viz.apiGet, __viz.API,
 *       __viz.g2d.loop.markRenderDirty（晚绑定）
 * 注册：__viz.g2d.pulses.{parseHitIdsFromSummary, spawnPulsesForNodes, pollSearchPulses}
 */
(function () {
  'use strict';

  const state = window.__viz.state;
  const _graphMaps = window.__viz._graphMaps;
  const _pulseSeenEventIds = window.__viz._pulseSeenEventIds;
  const { apiGet, API } = window.__viz;
  const { PULSE_WINDOW_SECONDS, PULSE_SEEN_CAP } = window.__viz.const;

  function parseHitIdsFromSummary(summary) {
    if (!summary) return [];
    const ids = [];
    const re = /\(id:\s*(\d+)\)/g;
    let m;
    while ((m = re.exec(summary)) !== null) {
      ids.push(parseInt(m[1], 10));
    }
    return ids;
  }

  function spawnPulsesForNodes(ids) {
    const g = state.graph;
    const now = performance.now();
    let any = false;
    for (const id of ids) {
      if (!_graphMaps.nodeIndex.has(id)) continue;
      g.pulses.push({ nodeId: id, startedAt: now });
      any = true;
    }
    if (any) window.__viz.g2d.loop.markRenderDirty();
  }

  async function pollSearchPulses() {
    if (!state.graph.built) return;
    try {
      const data = await apiGet(
        API + '/events/recent?tool=search&seconds=' + PULSE_WINDOW_SECONDS
      );
      const results = data.results || [];
      for (const ev of results) {
        if (_pulseSeenEventIds.has(ev.id)) continue;
        _pulseSeenEventIds.add(ev.id);
        const ids = parseHitIdsFromSummary(ev.result_summary);
        if (ids.length) spawnPulsesForNodes(ids);
      }
      if (_pulseSeenEventIds.size > PULSE_SEEN_CAP) {
        const drop = _pulseSeenEventIds.size - PULSE_SEEN_CAP;
        const it = _pulseSeenEventIds.values();
        for (let i = 0; i < drop; i++) _pulseSeenEventIds.delete(it.next().value);
      }
    } catch (_) {
      /* Silent — poll failures must never corrupt UI state. */
    }
  }

  // ---- 注册到 __viz 命名空间 ----
  window.__viz.g2d = window.__viz.g2d || {};
  window.__viz.g2d.pulses = { parseHitIdsFromSummary, spawnPulsesForNodes, pollSearchPulses };
})();
