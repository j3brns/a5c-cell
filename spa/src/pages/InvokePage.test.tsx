/* @vitest-environment jsdom */
import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { asyncAccepted, buildAgent } from "../test/testData";
import { InvokePage } from "./InvokePage";

// Define mocks and classes in vi.hoisted to ensure they're available during hoisting
const { navigateMock, requestMock, streamMock, ApiError } = vi.hoisted(() => {
    class ApiError extends Error {
        body: any;
        constructor(message: string, body: any) {
            super(message);
            this.name = "ApiError";
            this.body = body;
        }
    }
    return {
        navigateMock: vi.fn(),
        requestMock: vi.fn(),
        streamMock: vi.fn(),
        ApiError: ApiError
    };
});

vi.mock("../api/client", () => ({
    getApiClient: () => ({
        request: requestMock,
        stream: streamMock,
    }),
    ApiError: ApiError,
}));

vi.mock("../hooks/useJobPolling", () => ({
    useJobPolling: (jobId: string | null) => ({
        status: jobId === "job-777" ? { 
            jobId: "job-777", 
            status: "completed", 
            resultUrl: "https://example.test/result" 
        } : null,
        loading: false,
        error: jobId === "job-777" ? "polling warning" : null,
    }),
}));

vi.mock("../hooks/useSessionKeepalive", () => ({
    useSessionKeepalive: vi.fn(),
}));

// We'll mock useAgUiSession with a default implementation that can be overridden if needed
let agUiSessionMock = {
    status: "idle",
    bootstrap: null,
    messages: [],
    accumulatedText: "",
    sessionId: null,
    error: null,
    start: vi.fn(),
    disconnect: vi.fn(),
    reconnect: vi.fn(),
};

vi.mock("../hooks/useAgUiSession", () => ({
    useAgUiSession: () => agUiSessionMock,
}));

// Use actual useNavigate but wrap in MemoryRouter to capture navigation
vi.mock("react-router-dom", async () => {
    const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
    return {
        ...actual,
        useNavigate: () => navigateMock,
    };
});

function renderWithRouter(ui: React.ReactElement, { route = "/agents/echo-agent" } = {}) {
    return render(
        <MemoryRouter initialEntries={[route]}>
            <Routes>
                <Route path="/agents/:agentName" element={ui} />
                <Route path="/" element={<div>Catalogue</div>} />
            </Routes>
        </MemoryRouter>
    );
}

function getInvokeBody(): Record<string, unknown> {
    const call = requestMock.mock.calls.find(c => c[0].endsWith("/invoke"));
    const init = call?.[1] as { body?: string } | undefined;
    if (!init?.body) {
        throw new Error("Invoke request body not captured");
    }
    return JSON.parse(init.body) as Record<string, unknown>;
}

describe("InvokePage", () => {
    beforeEach(() => {
        vi.clearAllMocks();
        agUiSessionMock = {
            status: "idle",
            bootstrap: null,
            messages: [],
            accumulatedText: "",
            sessionId: null,
            error: null,
            start: vi.fn(),
            disconnect: vi.fn(),
            reconnect: vi.fn(),
        };
    });

    it("renders agent metadata from the deployed camelCase detail contract", async () => {
        requestMock.mockResolvedValueOnce(buildAgent("sync"));

        renderWithRouter(<InvokePage />);

        expect(await screen.findByText(/Invoke: echo-agent/i)).toBeInTheDocument();
        expect(screen.getAllByText(/sync/i)[0]).toBeInTheDocument();
    });

    it("sends sync invoke requests with contract-compatible input payload", async () => {
        requestMock.mockResolvedValueOnce(buildAgent("sync")).mockResolvedValueOnce({
            invocationId: "inv-1",
            agentName: "echo-agent",
            mode: "sync",
            status: "success",
            output: "hello",
        });

        renderWithRouter(<InvokePage />);

        const input = await screen.findByPlaceholderText(/type your prompt/i);
        fireEvent.change(input, { target: { value: "ping" } });
        fireEvent.click(screen.getByRole("button", { name: /invoke agent/i }));

        await waitFor(() => {
            expect(requestMock).toHaveBeenCalledTimes(2);
        });

        expect(getInvokeBody()).toEqual({ input: "ping" });
    });

    it("uses streaming invoke path with contract-compatible payload", async () => {
        requestMock.mockResolvedValueOnce(buildAgent("streaming"));
        streamMock.mockReturnValue(
            (async function* () {
                yield { data: "hello " };
                yield { data: "world" };
            })(),
        );

        renderWithRouter(<InvokePage />);

        const input = await screen.findByPlaceholderText(/type your prompt/i);
        fireEvent.change(input, { target: { value: "stream this" } });
        fireEvent.click(screen.getByRole("button", { name: /invoke agent/i }));

        await waitFor(() => {
            expect(streamMock).toHaveBeenCalledTimes(1);
        });

        expect(streamMock).toHaveBeenCalledWith("/v1/agents/echo-agent/invoke", expect.objectContaining({
            method: "POST",
            body: JSON.stringify({ input: "stream this" }),
        }));
    });

    it("handles async accepted responses and starts polling with jobId", async () => {
        requestMock.mockResolvedValueOnce(buildAgent("async")).mockResolvedValueOnce(asyncAccepted);

        renderWithRouter(<InvokePage />);

        const input = await screen.findByPlaceholderText(/type your prompt/i);
        fireEvent.change(input, { target: { value: "run async" } });
        fireEvent.click(screen.getByRole("button", { name: /invoke agent/i }));

        await waitFor(() => {
            expect(requestMock).toHaveBeenCalledTimes(2);
        });

        expect(getInvokeBody()).toEqual({ input: "run async" });
    });

    it("surfaces async contract error when accepted response has no job id", async () => {
        requestMock.mockResolvedValueOnce(buildAgent("async")).mockResolvedValueOnce({
            jobId: "",
            status: "accepted",
            mode: "async",
        });

        renderWithRouter(<InvokePage />);

        const input = await screen.findByPlaceholderText(/type your prompt/i);
        fireEvent.change(input, { target: { value: "run async without id" } });
        fireEvent.click(screen.getByRole("button", { name: /invoke agent/i }));

        expect(await screen.findByText(/Async invoke response missing jobId/i)).toBeInTheDocument();
    });

    it("shows fetch error when initial agent lookup fails", async () => {
        requestMock.mockRejectedValueOnce(new ApiError("agent lookup failed", { error: { message: "agent lookup failed" } }));

        renderWithRouter(<InvokePage />);

        expect(await screen.findByText(/agent lookup failed/i)).toBeInTheDocument();
    });

    it("renders async completion link and polling error details", async () => {
        requestMock.mockResolvedValueOnce(buildAgent("async")).mockResolvedValueOnce(asyncAccepted);

        renderWithRouter(<InvokePage />);

        const input = await screen.findByPlaceholderText(/type your prompt/i);
        fireEvent.change(input, { target: { value: "complete async" } });
        fireEvent.click(screen.getByRole("button", { name: /invoke agent/i }));

        expect(await screen.findByText(/View Results/i)).toBeInTheDocument();
        expect(screen.getByText(/polling warning/i)).toBeInTheDocument();
    });

    it("shows AG-UI badge and interactive button for AG-UI-capable agents", async () => {
        requestMock.mockResolvedValueOnce(buildAgent("streaming", { agUiEnabled: true }));

        renderWithRouter(<InvokePage />);

        expect(await screen.findByText(/AG-UI/i)).toBeInTheDocument();
        expect(screen.getByText(/Start Interactive Session/i)).toBeInTheDocument();
    });

    it("uses AG-UI session start for AG-UI-capable agents on invoke", async () => {
        agUiSessionMock.start = vi.fn();
        requestMock.mockResolvedValueOnce(buildAgent("streaming", { agUiEnabled: true }));

        renderWithRouter(<InvokePage />);

        const input = await screen.findByPlaceholderText(/type your prompt/i);
        fireEvent.change(input, { target: { value: "interactive test" } });
        fireEvent.click(screen.getByRole("button", { name: /invoke agent/i }));

        await waitFor(() => {
            expect(agUiSessionMock.start).toHaveBeenCalledWith("interactive test");
        });
        
        expect(requestMock).toHaveBeenCalledTimes(1); 
        expect(streamMock).not.toHaveBeenCalled();
    });

    it("shows AG-UI accumulated text via ResponseDisplay", async () => {
        agUiSessionMock.status = "connected";
        agUiSessionMock.accumulatedText = "AG-UI streamed output";
        agUiSessionMock.sessionId = "sess-1";
        
        requestMock.mockResolvedValueOnce(buildAgent("streaming", { agUiEnabled: true }));

        renderWithRouter(<InvokePage />);

        expect(await screen.findByText(/AG-UI streamed output/i)).toBeInTheDocument();
        expect(screen.getByText(/AG-UI session active/i)).toBeInTheDocument();
    });

    it("shows AG-UI error with retry option", async () => {
        agUiSessionMock.status = "error";
        agUiSessionMock.error = "AG-UI connection lost";
        
        requestMock.mockResolvedValueOnce(buildAgent("streaming", { agUiEnabled: true }));

        renderWithRouter(<InvokePage />);

        expect(await screen.findByText(/AG-UI connection lost/i)).toBeInTheDocument();
        expect(screen.getByText(/Retry AG-UI/i)).toBeInTheDocument();
    });

    it("navigates back to catalogue when back button is clicked", async () => {
        requestMock.mockResolvedValueOnce(buildAgent("sync"));

        renderWithRouter(<InvokePage />);

        const backLink = await screen.findByRole("link", { name: /back to catalogue/i });
        fireEvent.click(backLink);

        expect(navigateMock).toHaveBeenCalledWith("/");
    });
});
