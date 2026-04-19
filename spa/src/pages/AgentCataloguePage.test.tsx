/* @vitest-environment jsdom */
import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getApiClient } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { createApiClientMock, createAuthContextValue } from "../test/mockFactories";
import { catalogueMixedAgents, catalogueWithAgUiAgent } from "../test/testData";
import { AgentCataloguePage } from "./AgentCataloguePage";

vi.mock("../api/client", () => ({
  getApiClient: vi.fn(),
}));

vi.mock("../auth/useAuth", () => ({
  useAuth: vi.fn(),
}));

describe("AgentCataloguePage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useAuth).mockReturnValue(createAuthContextValue({
      isAuthenticated: true,
    }) as never);
  });

  it("renders agents on success", async () => {
    const request = vi.fn().mockResolvedValue(catalogueMixedAgents);
    vi.mocked(getApiClient).mockReturnValue(createApiClientMock({
      request,
    }) as never);

    render(
      <MemoryRouter>
        <AgentCataloguePage />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText("echo-agent")).toBeInTheDocument();
      expect(screen.getAllByText(/1\.0\.0/)[0]).toBeInTheDocument();
      expect(screen.getAllByText(/sync/i)[0]).toBeInTheDocument();
      expect(screen.getByText("research-agent")).toBeInTheDocument();
      expect(screen.getAllByText(/2\.1\.0/)[0]).toBeInTheDocument();
      expect(screen.getAllByText(/async/i)[0]).toBeInTheDocument();
      expect(screen.getByText("ops-agent")).toBeInTheDocument();
      expect(screen.getAllByText(/3\.0\.0/)[0]).toBeInTheDocument();
      expect(screen.getAllByText(/streaming/i)[0]).toBeInTheDocument();
      expect(screen.getAllByText(/Streaming/i)).not.toHaveLength(0);
    });
    expect(request).toHaveBeenCalledWith("/v1/agents");
  });

  it("renders empty state when no agents are returned", async () => {
    vi.mocked(getApiClient).mockReturnValue(createApiClientMock({
      request: vi.fn().mockResolvedValue({ items: [] }),
    }) as never);

    render(
      <MemoryRouter>
        <AgentCataloguePage />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText("No Agents Found")).toBeInTheDocument();
    });
  });

  it("renders error state when request fails", async () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.mocked(getApiClient).mockReturnValue(createApiClientMock({
      request: vi.fn().mockRejectedValue(new Error("catalogue failed")),
    }) as never);

    render(
      <MemoryRouter>
        <AgentCataloguePage />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText("catalogue failed")).toBeInTheDocument();
    });
    spy.mockRestore();
  });

  it("renders AG-UI badge for AG-UI-capable agents", async () => {
    const request = vi.fn().mockResolvedValue(catalogueWithAgUiAgent);
    vi.mocked(getApiClient).mockReturnValue(createApiClientMock({
      request,
    }) as never);

    render(
      <MemoryRouter>
        <AgentCataloguePage />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText("interactive-agent")).toBeInTheDocument();
      expect(screen.getByText("rest-only-agent")).toBeInTheDocument();
      // Only the AG-UI-capable agent should have the badge
      expect(screen.getAllByText("AG-UI")).toHaveLength(1);
    });
  });

  it("does not fetch catalogue when user is unauthenticated", async () => {
    vi.mocked(useAuth).mockReturnValue(createAuthContextValue({
      isAuthenticated: false,
    }) as never);
    const request = vi.fn();
    vi.mocked(getApiClient).mockReturnValue(createApiClientMock({
      request,
    }) as never);

    const { container } = render(
      <MemoryRouter>
        <AgentCataloguePage />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(container.querySelector(".animate-spin")).toBeInTheDocument();
    });
    expect(request).not.toHaveBeenCalled();
  });
});
