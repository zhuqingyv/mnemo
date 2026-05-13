import { ModelStatus, Language } from "../types";

interface StatusBarProps {
  status: ModelStatus;
  language: Language;
}

const STATUS_TEXT: Record<ModelStatus, Record<Language, string>> = {
  faq: {
    "zh-CN": "FAQ 模式",
    en: "FAQ Mode",
    "zh-TW": "FAQ 模式",
  },
  loading: {
    "zh-CN": "本地模型加载中...",
    en: "Loading local model...",
    "zh-TW": "本地模型載入中...",
  },
  available: {
    "zh-CN": "本地模型可用",
    en: "Local model ready",
    "zh-TW": "本地模型可用",
  },
  degraded: {
    "zh-CN": "模型不可用，已降级为 FAQ 模式",
    en: "Model unavailable, degraded to FAQ",
    "zh-TW": "模型不可用，已降級為 FAQ 模式",
  },
};

const SECURITY_NOTICE: Record<Language, string> = {
  "zh-CN":
    "仅查询 Mnemo 公共知识 · 不读取私人记忆 · 不上传问题 · 不执行命令",
  en: "Public knowledge only · No private memory · No upload · No commands",
  "zh-TW":
    "僅查詢 Mnemo 公共知識 · 不讀取私人記憶 · 不上傳問題 · 不執行命令",
};

export function StatusBar({ status, language }: StatusBarProps) {
  return (
    <div className="guide-status-bar">
      <span className={`guide-status-badge guide-status-${status}`}>
        {STATUS_TEXT[status][language]}
      </span>
      <span className="guide-security-notice">
        {SECURITY_NOTICE[language]}
      </span>
    </div>
  );
}
