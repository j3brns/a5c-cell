import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useAgUiSession } from "./useAgUiSession";
import type { SseEvent } from "../api/client";
import { agUiBootstrapResponse } from "../test/testData";

const bootstrapMock = vi.fn();
const streamMock = vi.fn();
const getAccessTokenMock = vi.fn(async () => "token");

vi.mock("../api/client", () => ({
  getApiClient: vi.fn(() => ({
    bootstrapAgUiSession: bootstrapMock,
    stream: streamMock,
  })),
}));

async function* createSseStream(events: SseEvent[]): AsyncGenerator<SseEvent> {
  for (const event of events) {
    yield event;
  }
}

describe("useAgUiSession", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("starts in idle status", () => {
    const { result } = renderHook(() => useAgUiSession("echo-agent", getAccessTokenMock));
    expect(result.current.status).toBe("idle");
    expect(result.current.bootstrap).toBeNull();
    expect(result.current.messages).toEqual([]);
    expect(result.current.accumulatedText).toBe("");
    expect(result.current.sessionId).toBeNull();
    expect(result.current.error).toBeNull();
  });

  it("bootstraps and connects to AG-UI SSE stream", async () => {
    bootstrapMock.mockResolvedValue(agUiBootstrapResponse);
    streamMock.mockReturnValue(
      createSseStream([
        {
          event: "message",
          data: '{"type":"text","content":"hello "}',
          raw: 'data: {"type":"text","content":"hello "}',
        },
        {
          event: "message",
          data: '{"type":"text","content":"world"}',
          raw: 'data: {"type":"text","content":"world"}',
        },
        {
          event: "message",
          data: "[DONE]",
          raw: "data: [DONE]",
        },
      ]),
    );

    const { result } = renderHook(() => useAgUiSession("echo-agent", getAccessTokenMock));

    await act(async () => {
      await result.current.start("test prompt");
    });

    expect(bootstrapMock).toHaveBeenCalledWith("echo-agent", {});
    expect(streamMock).toHaveBeenCalledTimes(1);
    expect(streamMock.mock.calls[0]?.[0]).toBe(agUiBootstrapResponse.connectUrl);
    expect(streamMock.mock.calls[0]?.[1]).toMatchObject({
      method: "GET",
      signal: expect.any(AbortSignal),
    });
    expect(result.current.bootstrap).toEqual(agUiBootstrapResponse);
    expect(result.current.sessionId).toBe("sess-agui-001");
    expect(result.current.accumulatedText).toBe("hello world");
    expect(result.current.status).toBe("closed");
  });

  it("sets error status when bootstrap fails", async () => {
    bootstrapMock.mockRejectedValue(new Error("bootstrap failed"));

    const { result } = renderHook(() => useAgUiSession("echo-agent", getAccessTokenMock));

    await act(async () => {
      await result.current.start("test prompt");
    });

    expect(result.current.status).toBe("error");
    expect(result.current.error).toBe("bootstrap failed");
  });

  it("sets error status when SSE connection returns non-ok", async () => {
    bootstrapMock.mockResolvedValue(agUiBootstrapResponse);
    streamMock.mockImplementation(async function* () {
      yield* [];
      throw new Error("AG-UI connection failed with HTTP 502");
    });

    const { result } = renderHook(() => useAgUiSession("echo-agent", getAccessTokenMock));

    await act(async () => {
      await result.current.start("test prompt");
    });

    expect(result.current.status).toBe("error");
    expect(result.current.error).toContain("502");
  });

  it("does nothing when agentName is undefined", async () => {
    const { result } = renderHook(() => useAgUiSession(undefined, getAccessTokenMock));

    await act(async () => {
      await result.current.start("test");
    });

    expect(bootstrapMock).not.toHaveBeenCalled();
    expect(result.current.status).toBe("idle");
  });

  it("accumulates raw text data from non-JSON SSE events", async () => {
    bootstrapMock.mockResolvedValue(agUiBootstrapResponse);
    streamMock.mockReturnValue(
      createSseStream([
        {
          event: "text",
          data: "raw chunk",
          raw: "event: text\ndata: raw chunk",
        },
        {
          event: "message",
          data: "[DONE]",
          raw: "data: [DONE]",
        },
      ]),
    );

    const { result } = renderHook(() => useAgUiSession("echo-agent", getAccessTokenMock));

    await act(async () => {
      await result.current.start("test");
    });

    expect(result.current.accumulatedText).toBe("raw chunk");
  });

  it("disconnect aborts the stream and sets status to closed", async () => {
    bootstrapMock.mockResolvedValue(agUiBootstrapResponse);

    streamMock.mockImplementation(async function* (_url: string, init?: RequestInit) {
      const signal = init?.signal;
      yield {
        event: "message",
        data: '{"type":"text","content":"start"}',
        raw: 'data: {"type":"text","content":"start"}',
      };
      await new Promise<void>((resolve) => {
        if (signal?.aborted) {
          resolve();
          return;
        }

        signal?.addEventListener("abort", () => resolve(), { once: true });
      });
    });

    const { result } = renderHook(() => useAgUiSession("echo-agent", getAccessTokenMock));

    await act(async () => {
      const pendingStart = result.current.start("test");
      await new Promise((r) => setTimeout(r, 10));
      result.current.disconnect();
      await pendingStart;
    });

    expect(result.current.status).toBe("closed");
  });
});
