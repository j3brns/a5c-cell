import { useCallback, useEffect, useRef, useState } from "react";
import { getApiClient, type AccessTokenProvider } from "../api/client";
import type { AgentAgUiBootstrapResponseDto } from "../api/contracts";

export type AgUiSessionStatus =
  | "idle"
  | "bootstrapping"
  | "connecting"
  | "connected"
  | "reconnecting"
  | "error"
  | "closed";

export type AgUiMessage = {
  id?: string;
  event: string;
  data: string;
  timestamp: number;
};

export type AgUiSessionState = {
  status: AgUiSessionStatus;
  bootstrap: AgentAgUiBootstrapResponseDto | null;
  messages: AgUiMessage[];
  accumulatedText: string;
  sessionId: string | null;
  error: string | null;
};

export type AgUiSessionActions = {
  start: (input: string) => Promise<void>;
  disconnect: () => void;
  reconnect: () => Promise<void>;
};

const MAX_RECONNECT_ATTEMPTS = 3;
const RECONNECT_DELAY_MS = 2000;

export function useAgUiSession(
  agentName: string | undefined,
  getAccessToken: AccessTokenProvider,
): AgUiSessionState & AgUiSessionActions {
  const [status, setStatus] = useState<AgUiSessionStatus>("idle");
  const [bootstrap, setBootstrap] = useState<AgentAgUiBootstrapResponseDto | null>(null);
  const [messages, setMessages] = useState<AgUiMessage[]>([]);
  const [accumulatedText, setAccumulatedText] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const reconnectAttemptRef = useRef(0);
  const lastInputRef = useRef<string>("");

  // Clean up on unmount
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const consumeSseStream = useCallback(
    async (connectUrl: string, signal: AbortSignal) => {
      const client = getApiClient(getAccessToken);

      setStatus("connected");
      reconnectAttemptRef.current = 0;

      for await (const sseEvent of client.stream(connectUrl, {
        method: "GET",
        signal,
      })) {
        const message: AgUiMessage = {
          id: sseEvent.id,
          event: sseEvent.event,
          data: sseEvent.data,
          timestamp: Date.now(),
        };

        setMessages((prev) => [...prev, message]);

        if (sseEvent.data === "[DONE]") {
          setStatus("closed");
          return;
        }

        try {
          const payload = JSON.parse(sseEvent.data) as Record<string, unknown>;
          if (payload.type === "text" && typeof payload.content === "string") {
            setAccumulatedText((prev) => prev + payload.content);
          } else if (typeof payload.content === "string") {
            setAccumulatedText((prev) => prev + payload.content);
          } else if (typeof payload.output === "string") {
            setAccumulatedText((prev) => prev + payload.output);
          }
        } catch {
          // Non-JSON data — append raw
          if (sseEvent.event === "message" || sseEvent.event === "text") {
            setAccumulatedText((prev) => prev + sseEvent.data);
          }
        }
      }

      setStatus("closed");
    },
    [getAccessToken],
  );

  const attemptReconnect = useCallback(
    async function reconnectWithBackoff(bootstrapData: AgentAgUiBootstrapResponseDto) {
      if (reconnectAttemptRef.current >= MAX_RECONNECT_ATTEMPTS) {
        setStatus("error");
        setError(
          `AG-UI connection lost after ${MAX_RECONNECT_ATTEMPTS} reconnect attempts. ` +
            "You can retry the request using the standard invoke path.",
        );
        return;
      }

      reconnectAttemptRef.current += 1;
      setStatus("reconnecting");

      await new Promise((resolve) => setTimeout(resolve, RECONNECT_DELAY_MS));

      if (abortRef.current?.signal.aborted) return;

      try {
        const abortController = new AbortController();
        abortRef.current = abortController;
        await consumeSseStream(bootstrapData.connectUrl, abortController.signal);
      } catch (err) {
        if ((err as Error).name === "AbortError") return;
        await reconnectWithBackoff(bootstrapData);
      }
    },
    [consumeSseStream],
  );

  const start = useCallback(
    async (input: string) => {
      if (!agentName) return;

      // Reset state
      abortRef.current?.abort();
      setMessages([]);
      setAccumulatedText("");
      setError(null);
      reconnectAttemptRef.current = 0;
      lastInputRef.current = input;

      setStatus("bootstrapping");

      try {
        const client = getApiClient(getAccessToken);
        const bootstrapResponse = await client.bootstrapAgUiSession(agentName, {
          sessionId: sessionId ?? undefined,
        });

        setBootstrap(bootstrapResponse);
        setSessionId(bootstrapResponse.sessionId);

        setStatus("connecting");

        const abortController = new AbortController();
        abortRef.current = abortController;

        await consumeSseStream(bootstrapResponse.connectUrl, abortController.signal);
      } catch (err) {
        if ((err as Error).name === "AbortError") return;

        const message =
          err instanceof Error ? err.message : "AG-UI session failed to start";
        setStatus("error");
        setError(message);
      }
    },
    [agentName, getAccessToken, sessionId, consumeSseStream],
  );

  const disconnect = useCallback(() => {
    abortRef.current?.abort();
    setStatus("closed");
  }, []);

  const reconnect = useCallback(async () => {
    if (!bootstrap) {
      setError("No active session to reconnect");
      return;
    }
    reconnectAttemptRef.current = 0;
    setError(null);

    try {
      setStatus("connecting");
      const abortController = new AbortController();
      abortRef.current = abortController;
      await consumeSseStream(bootstrap.connectUrl, abortController.signal);
    } catch (err) {
      if ((err as Error).name === "AbortError") return;
      await attemptReconnect(bootstrap);
    }
  }, [bootstrap, consumeSseStream, attemptReconnect]);

  return {
    status,
    bootstrap,
    messages,
    accumulatedText,
    sessionId,
    error,
    start,
    disconnect,
    reconnect,
  };
}
