/**
 * js/components/app.js
 *
 * 职责：顶层 App 组件 + mount 到 #app
 * 依赖：__viz.NV, __viz.DomTags, __viz.state, __viz.t
 *       __viz.loader.{probeHealth, loadStats, loadKnowledge, loadRelations}
 *       __viz.polling.startPolling
 *       __viz.search.runSearch
 *       __viz.comp.{Sidebar, Topbar, Dashboard, ToolChart, KnowledgeList,
 *                    GraphView, Graph3DView, DetailPanel}
 * 注册：__viz.comp.App，随后 mount(App(), #app)
 */
(function () {
  'use strict';

  const { component, mount, Show, onMounted, watch } = window.__viz.NV;
  const { div, span } = window.__viz.DomTags;
  const state = window.__viz.state;
  const t = window.__viz.t;

  const App = component(() => {
    const {
      Sidebar, Topbar, Dashboard, ToolChart, KnowledgeList,
      GraphView, Graph3DView, DetailPanel,
    } = window.__viz.comp;

    // View lifecycle: cleanup on leave, search sync on active view
    watch(() => state.view, (next, prev) => {
      if (prev === '2d' && next !== '2d') {
        var stop = window.__viz.g2d && window.__viz.g2d.loop && window.__viz.g2d.loop.stopGraphLoop;
        if (typeof stop === 'function') stop();
      }
      if (prev === '3d' && next !== '3d') {
        var deact = window.__viz.g3d && window.__viz.g3d.lifecycle && window.__viz.g3d.lifecycle.deactivate;
        if (typeof deact === 'function') deact();
      }
    });
    watch(() => state.searchResults, () => {
      if (state.view === '2d') {
        var fn = window.__viz.g2d && window.__viz.g2d.updateSearchMatch;
        if (typeof fn === 'function') fn();
      }
      if (state.view === '3d') {
        var fn3 = window.__viz.g3d && window.__viz.g3d.lifecycle && window.__viz.g3d.lifecycle.updateSearchMatch;
        if (typeof fn3 === 'function') fn3();
      }
    });
    watch(() => state.knowledge.length, (n) => {
      if (state.view === '2d' && n > 0 && !state.graph.built && Array.isArray(state._rawRelations)) {
        window.__viz.g2d.build.buildGraph();
      }
    });

    onMounted(async () => {
      const { probeHealth, loadStats, loadKnowledge, loadRelations } = window.__viz.loader;
      await probeHealth();
      await Promise.all([loadStats(), loadKnowledge(), loadRelations()]);
      // All data loaded — trigger graph build if in 2D view
      if (state.view === '2d' && state.knowledge.length > 0 && !state.graph.built) {
        window.__viz.g2d.build.buildGraph();
      }
      window.__viz.polling.startPolling();

      const urlParams = new URLSearchParams(location.search);
      const urlView = urlParams.get('view');
      if (urlView === '2d' || urlView === '3d') state.view = urlView;
      const urlQ = urlParams.get('q');
      if (urlQ) {
        state.searchQuery = urlQ;
        const searchInput = document.querySelector('.search-wrap input')
          || document.querySelector('input[type="text"]');
        if (searchInput) searchInput.value = urlQ;
        window.__viz.search.runSearch(urlQ);
      }
    });

    // 标题文本：t() 不是 reactive，切语言需刷新；直接调用得字符串，避开动态文本 Bug 4
    const sectionHead = (titleText, hintText, extraTitleNode) =>
      div().class('flex items-baseline justify-between')(
        extraTitleNode
          ? span().class('text-lg font-bold text-text-0')(titleText + ' ', extraTitleNode)
          : span().class('text-lg font-bold text-text-0')(titleText),
        span().class('text-xs text-text-3')(hintText),
      );

    return div().class('flex min-h-screen bg-surface-0 text-text-0 font-mono')(
      Sidebar(),
      div().class('flex-1 flex flex-col ml-14 min-w-0')(
        Topbar(),
        Show(() => state.view === 'list', {
          when: () => div().class('flex-1 overflow-y-auto px-8 py-6 space-y-8')(
            sectionHead(t('dash.overview'), 'GET /api/v1/stats'),
            Dashboard(),
            sectionHead(t('tools.title'), t('tools.subtitle')),
            ToolChart(),
            div().class('flex items-baseline justify-between')(
              span().class('text-lg font-bold text-text-0')(
                t('list.title') + ' ',
                span().class('text-text-3 font-normal')(
                  () => String(state.knowledge.length),
                ),
              ),
              span().class('text-xs text-text-3')(() =>
                state.searchResults !== null
                  ? 'GET /api/v1/knowledge/search?query=' + encodeURIComponent(state.searchQuery || '')
                  : 'GET /api/v1/knowledge',
              ),
            ),
            KnowledgeList(),
          ),
        }),
        Show(() => state.view === '2d', { when: () => GraphView() }),
        Show(() => state.view === '3d', { when: () => Graph3DView() }),
        DetailPanel(),
      ),
    );
  });

  window.__viz.comp = window.__viz.comp || {};
  window.__viz.comp.App = App;

  mount(App(), document.getElementById('app'));
})();
