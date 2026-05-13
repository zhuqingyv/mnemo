import { useState, useCallback } from "react";
import { ChatMessage, AskResponse, ModelStatus } from "../types";

type SendFn = <T = unknown>(method: string, params?: unknown) => Promise<T>;

export function useGuide(send: SendFn) {
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: "welcome",
      role: "assistant",
      content:
        "我是 Mnemo Guide，本地说明书助手。我只能回答 Mnemo 相关问题。试试上面的快捷问题吧。",
      timestamp: Date.now(),
    },
  ]);
  const [loading, setLoading] = useState(false);
  const [modelStatus, setModelStatus] = useState<ModelStatus>("faq");
  const [error, setError] = useState<string | null>(null);

  const ask = useCallback(async (question: string) => {
    const userMsg: ChatMessage = {
      id: `u-${Date.now()}`,
      role: "user",
      content: question,
      timestamp: Date.now(),
    };
    setMessages((prev) => [...prev, userMsg]);
    setLoading(true);
    setError(null);

    try {
      const data = await send<AskResponse>("guide.ask", { question });

      const assistantMsg: ChatMessage = {
        id: `a-${Date.now()}`,
        role: "assistant",
        content: data.answer,
        commands: data.commands,
        source: data.source,
        timestamp: Date.now(),
      };
      setMessages((prev) => [...prev, assistantMsg]);
      setModelStatus(data.model_used ? "available" : "faq");
    } catch (e) {
      const errMsg = String(e);
      setError(errMsg);
      const errChatMsg: ChatMessage = {
        id: `err-${Date.now()}`,
        role: "assistant",
        content: "抱歉，说明书助手暂时无法响应。请确保 Mnemo 正在运行。",
        timestamp: Date.now(),
      };
      setMessages((prev) => [...prev, errChatMsg]);
    } finally {
      setLoading(false);
    }
  }, [send]);

  const clearMessages = useCallback(() => {
    setMessages([
      {
        id: "welcome",
        role: "assistant",
        content: "我是 Mnemo Guide，本地说明书助手。",
        timestamp: Date.now(),
      },
    ]);
  }, []);

  return { messages, loading, modelStatus, error, ask, clearMessages, setModelStatus };
}
