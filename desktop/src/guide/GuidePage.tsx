import { useRef, useEffect } from "react";
import { useGuide } from "./hooks/useGuide";
import { QuickQuestions } from "./components/QuickQuestions";
import { QuestionInput } from "./components/QuestionInput";
import { AnswerView } from "./components/AnswerView";
import { StatusBar } from "./components/StatusBar";
import { Language } from "./types";
import "./GuidePage.css";

interface GuidePageProps {
  language: Language;
  send: <T = unknown>(method: string, params?: unknown) => Promise<T>;
  onBack: () => void;
}

const PLACEHOLDERS: Record<Language, string> = {
  "zh-CN": "输入你的问题，例如：怎么安装 Mnemo？",
  en: "Ask anything about Mnemo, e.g.: How to install?",
  "zh-TW": "輸入你的問題，例如：怎麼安裝 Mnemo？",
};

export function GuidePage({ language, send, onBack }: GuidePageProps) {
  const { messages, loading, modelStatus, error, ask } = useGuide(send);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  return (
    <div className="guide-page">
      <div className="guide-header">
        <button className="guide-back-btn" onClick={onBack} type="button">
          ← 返回
        </button>
        <h1 className="guide-title">Mnemo Guide</h1>
        <p className="guide-subtitle">
          {language === "zh-CN"
            ? "本地说明书助手，仅查询 Mnemo 公共知识"
            : language === "zh-TW"
              ? "本地說明書助手，僅查詢 Mnemo 公共知識"
              : "Local manual assistant, public knowledge only"}
        </p>
      </div>

      <StatusBar status={modelStatus} language={language} />

      <div className="guide-chat-area">
        <QuickQuestions onSelect={ask} language={language} />

        <div className="guide-messages">
          {messages.map((msg) => (
            <AnswerView key={msg.id} message={msg} />
          ))}
          {loading && (
            <div className="guide-message guide-message-assistant">
              <div className="guide-loading-dots">
                <span>.</span>
                <span>.</span>
                <span>.</span>
              </div>
            </div>
          )}
          {error && <div className="guide-error">{error}</div>}
          <div ref={bottomRef} />
        </div>
      </div>

      <QuestionInput
        onSend={ask}
        disabled={loading}
        placeholder={PLACEHOLDERS[language]}
      />
    </div>
  );
}
