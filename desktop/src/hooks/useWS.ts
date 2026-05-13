import { useCallback, useEffect, useRef, useState } from "react";

// ── JSON-RPC 2.0 types ──────────────────────────────────────────

export interface RpcRequest {
  jsonrpc: "2.0";
  id: number;
  method: string;
  params?: unknown;
}

export interface RpcResponse {
  jsonrpc: "2.0";
  id: number;
  result: unknown;
}

export interface RpcError {
  jsonrpc: "2.0";
  id: number;
  error: {
    code: number;
    message: string;
    data?: unknown;
  };
}

export interface RpcNotification {
  jsonrpc: "2.0";
  method: string;
  params?: unknown;
}

type RpcOutgoing = RpcResponse | RpcError | RpcNotification;

type EventHandler = (params: unknown) => void;

interface PendingRequest {
  resolve: (value: unknown) => void;
  reject: (reason: Error) => void;
}

const DEFAULT_URL = "ws://127.0.0.1:8788/ws";

/**
 * WebSocket hook providing JSON-RPC 2.0 duplex communication
 * with the Interface Layer.
 */
export function useWS(url: string = DEFAULT_URL) {
  const [ready, setReady] = useState(false);
  const [connected, setConnected] = useState(false);

  const wsRef = useRef<WebSocket | null>(null);
  const idRef = useRef(0);
  const pendingRef = useRef<Map<number, PendingRequest>>(new Map());
  const listenersRef = useRef<Map<string, Set<EventHandler>>>(new Map());

  // ── Socket lifecycle ───────────────────────────────────────

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      setReady(true);
    };

    ws.onclose = () => {
      setConnected(false);
      setReady(false);
      // Reconnect after 1s
      setTimeout(connect, 1000);
    };

    ws.onerror = () => {
      ws.close();
    };

    ws.onmessage = (event) => {
      try {
        const msg: RpcOutgoing = JSON.parse(event.data);

        // Response to a pending request
        if ("id" in msg && pendingRef.current.has(msg.id)) {
          const pending = pendingRef.current.get(msg.id)!;
          pendingRef.current.delete(msg.id);

          if ("error" in msg) {
            pending.reject(new Error(msg.error.message));
          } else {
            pending.resolve((msg as RpcResponse).result);
          }
          return;
        }

        // Push notification / event
        if ("method" in msg && !("id" in msg)) {
          const notif = msg as RpcNotification;
          const handlers = listenersRef.current.get(notif.method);
          if (handlers) {
            handlers.forEach((fn) => fn(notif.params));
          }
        }
      } catch {
        // Ignore malformed messages
      }
    };
  }, [url]);

  // Auto-connect on mount, disconnect on unmount
  useEffect(() => {
    connect();
    return () => {
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [connect]);

  // ── Send a request and wait for response ───────────────────

  const send = useCallback(<T = unknown>(method: string, params?: unknown): Promise<T> => {
    return new Promise((resolve, reject) => {
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        reject(new Error("WebSocket not connected"));
        return;
      }

      const id = ++idRef.current;
      const request: RpcRequest = {
        jsonrpc: "2.0",
        id,
        method,
        params: params ?? {},
      };

      pendingRef.current.set(id, {
        resolve: resolve as (value: unknown) => void,
        reject,
      });

      ws.send(JSON.stringify(request));
    });
  }, []);

  // ── Listen for push events ─────────────────────────────────

  const on = useCallback((event: string, handler: EventHandler) => {
    if (!listenersRef.current.has(event)) {
      listenersRef.current.set(event, new Set());
    }
    listenersRef.current.get(event)!.add(handler);

    // Return unsubscribe function
    return () => {
      listenersRef.current.get(event)?.delete(handler);
    };
  }, []);

  const off = useCallback((event: string, handler: EventHandler) => {
    listenersRef.current.get(event)?.delete(handler);
  }, []);

  return { send, on, off, ready, connected };
}
