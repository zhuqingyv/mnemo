/**
 * js/components/sidebar.js
 *
 * 职责：左侧垂直图标导航栏 — logo + home/2D/3D/settings，选中态高亮
 * 依赖：__viz.NV (Dom.img), __viz.DomTags (div/span), __viz.state,
 *       __viz.util.syncViewToUrl
 * 注册：__viz.comp.Sidebar
 *
 * 设计稿：深紫黑底 (#080b16)、窄 56px、图标居中、选中态蓝色面板 + glow。
 * view 映射：
 *   home     → state.view = 'list'
 *   graph2d  → state.view = '2d'
 *   graph3d  → state.view = '3d'
 *   settings → 暂未接入任何 view（占位，点击无副作用）
 */
(function () {
  'use strict';

  const { div, span } = window.__viz.DomTags;
  const state = window.__viz.state;

  const NAV_ITEMS = [
    { key: 'home',     label: '首页', glyph: '⌂', view: 'list' },
    { key: 'graph2d',  label: '2D 图谱', glyph: '◉', view: '2d' },
    { key: 'graph3d',  label: '3D 图谱', glyph: '◈', view: '3d' },
    { key: 'settings', label: '设置', glyph: '⚙', view: null  },
  ];

  const ITEM_BASE =
    'w-10 h-10 flex items-center justify-center rounded-xl text-[18px] ' +
    'cursor-pointer transition-all select-none';
  const ITEM_ACTIVE =
    'bg-brand-blue/15 text-brand-blue shadow-glow-sm ' +
    'ring-1 ring-brand-blue/40';
  const ITEM_IDLE =
    'text-text-2 hover:text-text-0 hover:bg-surface-2';

  function navItem(item) {
    return div()
      .class(() => {
        const active = item.view !== null && state.view === item.view;
        return ITEM_BASE + ' ' + (active ? ITEM_ACTIVE : ITEM_IDLE);
      })
      .title(item.label)
      .onClick(() => {
        if (item.view === null) return; // settings 占位
        if (state.view === item.view) return;
        state.view = item.view;
        const sync = window.__viz.util && window.__viz.util.syncViewToUrl;
        if (typeof sync === 'function') sync();
      })(item.glyph);
  }

  function Sidebar() {
    return div().class(
      'fixed left-0 top-0 bottom-0 w-14 z-[60] ' +
      'bg-[#080b16] border-r border-[#1a1f35] ' +
      'flex flex-col items-center pt-3 pb-3 gap-1.5'
    )(
      // logo — 与 topbar 同一份 /viz/logo.png
      window.__viz.NV.Dom.img()
        .class('w-8 h-8 mb-3 drop-shadow-[0_0_10px_rgba(124,58,237,0.55)]')
        .src('/viz/logo.png')
        .alt('mnemo')(),

      // 分隔线
      div().class('w-6 h-px bg-[#1a1f35] mb-1')(),

      // nav items
      ...NAV_ITEMS.map(navItem),

      // spacer
      div().class('flex-1')(),

      // language switcher
      div().class('flex flex-col items-center gap-1 mb-2')(
        ...['zh-CN', 'zh-TW', 'en'].map(function(lang) {
          var label = lang === 'zh-CN' ? '简' : lang === 'zh-TW' ? '繁' : 'En';
          return div()
            .class(() => {
              var active = window.__viz.getLang() === lang;
              return 'w-8 h-6 flex items-center justify-center rounded text-[10px] cursor-pointer transition ' +
                (active ? 'bg-brand-purple/20 text-brand-purple' : 'text-text-3 hover:text-text-0 hover:bg-surface-2');
            })
            .onClick(function() {
              window.__viz.setLang(lang);
              location.reload();
            })(label);
        })
      ),

      // product version
      span().class('text-text-3 text-[9px] tracking-wider')(() =>
        state.mnemoVersion ? 'v' + state.mnemoVersion : ''
      ),
    );
  }

  // ---- 注册到 __viz ----
  window.__viz.comp = window.__viz.comp || {};
  window.__viz.comp.Sidebar = Sidebar;
})();
