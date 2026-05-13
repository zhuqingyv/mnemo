import {
  QUICK_QUESTIONS_ZH,
  QUICK_QUESTIONS_EN,
  QUICK_QUESTIONS_ZH_TW,
  Language,
} from "../types";

interface QuickQuestionsProps {
  onSelect: (question: string) => void;
  language: Language;
}

export function QuickQuestions({ onSelect, language }: QuickQuestionsProps) {
  const questions =
    language === "en"
      ? QUICK_QUESTIONS_EN
      : language === "zh-TW"
        ? QUICK_QUESTIONS_ZH_TW
        : QUICK_QUESTIONS_ZH;

  return (
    <div className="guide-quick-questions">
      {questions.map((q) => (
        <button
          key={q}
          className="guide-quick-btn"
          onClick={() => onSelect(q)}
          type="button"
        >
          {q}
        </button>
      ))}
    </div>
  );
}
