import { useState, useRef, FormEvent } from "react";

interface QuestionInputProps {
  onSend: (question: string) => void;
  disabled: boolean;
  placeholder: string;
}

export function QuestionInput({ onSend, disabled, placeholder }: QuestionInputProps) {
  const [value, setValue] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setValue("");
    inputRef.current?.focus();
  }

  return (
    <form className="guide-input-form" onSubmit={handleSubmit}>
      <input
        ref={inputRef}
        className="guide-input"
        type="text"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        autoFocus
      />
      <button
        className="guide-send-btn"
        type="submit"
        disabled={disabled || !value.trim()}
      >
        {disabled ? "..." : ">"}
      </button>
    </form>
  );
}
