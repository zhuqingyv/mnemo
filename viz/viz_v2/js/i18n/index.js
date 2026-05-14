(function(){
  window.__viz = window.__viz || {};
  window.__viz.i18n = window.__viz.i18n || {};

  var SUPPORTED = ['zh-CN', 'zh-TW', 'en'];
  var DEFAULT_LANG = 'zh-CN';

  function normalize(lang) {
    if (!lang) return DEFAULT_LANG;
    if (SUPPORTED.indexOf(lang) >= 0) return lang;
    var lower = String(lang).toLowerCase();
    if (lower.indexOf('zh-tw') === 0 || lower.indexOf('zh-hk') === 0 || lower.indexOf('zh-hant') === 0) return 'zh-TW';
    if (lower.indexOf('zh') === 0) return 'zh-CN';
    if (lower.indexOf('en') === 0) return 'en';
    return DEFAULT_LANG;
  }

  var _lang = normalize(localStorage.getItem('mnemo_lang') || navigator.language || DEFAULT_LANG);

  function t(key) {
    var dict = window.__viz.i18n[_lang] || window.__viz.i18n[DEFAULT_LANG] || {};
    return dict[key] != null ? dict[key] : key;
  }
  function setLang(lang) {
    var next = normalize(lang);
    _lang = next;
    try { localStorage.setItem('mnemo_lang', next); } catch (e) {}
  }
  function getLang() { return _lang; }
  function listLangs() { return SUPPORTED.slice(); }

  window.__viz.t = t;
  window.__viz.setLang = setLang;
  window.__viz.getLang = getLang;
  window.__viz.listLangs = listLangs;
})();
