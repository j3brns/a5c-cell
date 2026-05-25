/* @vitest-environment jsdom */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getApiClient } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { createApiClientMock, createAuthContextValue } from "../test/mockFactories";
import { healthFail, healthOk, quotaRows, tenantRows } from "../test/testData";
import { AdminPage } from "./AdminPage";

const { notifyMock, openMock, confirmMock } = vi.hoisted(() => ({
  notifyMock: vi.fn(),
  openMock: vi.fn(),
  confirmMock: vi.fn(),
}));

vi.mock("../api/client", () => ({
  getApiClient: vi.fn(),
}));

vi.mock("../auth/useAuth", () => ({
  useAuth: vi.fn(),
}));

vi.mock("../components/Notifications", () => ({
  useNotifications: vi.fn(() => ({
    notify: notifyMock,
  })),
}));

describe("AdminPage", () => {
  const richTenantRows = {
    items: [
      {
        tenantId: "t-001",
        appId: "app-001",
        displayName: "Acme",
        tier: "premium",
        status: "active",
        runtimeRegion: "eu-west-2",
        accountId: "123456789012",
        monthlyBudgetUsd: 250,
      },
      {
        tenantId: "t-002",
        appId: "app-002",
        displayName: "Beta",
        tier: "basic",
        status: "suspended",
        runtimeRegion: "eu-west-2",
        accountId: "210987654321",
        monthlyBudgetUsd: 50,
      },
    ],
  };

  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("open", openMock);
    vi.stubGlobal("confirm", confirmMock);
    vi.mocked(useAuth).mockReturnValue(createAuthContextValue({
      isAuthenticated: true,
      account: {
        idTokenClaims: { roles: ["Platform.Admin"] },
      } as never,
    }) as never);
  });

  afterEach(() => {
    cleanup();
  });

  async function renderAdminPageWithData(request: ReturnType<typeof vi.fn>) {
    vi.mocked(getApiClient).mockReturnValue(createApiClientMock({ request }) as never);

    render(<AdminPage />);

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Platform Admin" })).toBeInTheDocument();
      expect(screen.getByText("Acme")).toBeInTheDocument();
    });
  }

  it("renders health, tenant, and quota data", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(healthOk)
      .mockResolvedValueOnce(tenantRows)
      .mockResolvedValueOnce(quotaRows)
      .mockResolvedValueOnce({ tenants: [] })
      .mockResolvedValueOnce({ events: [] })
      .mockResolvedValueOnce(null);
    vi.mocked(getApiClient).mockReturnValue(createApiClientMock({ request }) as never);

    render(<AdminPage />);

    await waitFor(() => {
      expect(screen.getByText("Platform Health")).toBeInTheDocument();
      expect(screen.getByText("Acme")).toBeInTheDocument();
      expect(screen.getByText("Beta")).toBeInTheDocument();
      expect(screen.getAllByText((value) => value.includes("ConcurrentSessions"))).toHaveLength(2);
      expect(screen.getByText("92%")).toBeInTheDocument();
    });
    expect(request).toHaveBeenNthCalledWith(1, "/v1/health");
    expect(request).toHaveBeenNthCalledWith(2, "/v1/tenants");
    expect(request).toHaveBeenNthCalledWith(3, "/v1/platform/quota");
  });

  it("renders empty admin sections when API returns empty arrays", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(healthFail)
      .mockResolvedValueOnce({ items: [] })
      .mockResolvedValueOnce({ utilisation: [] })
      .mockResolvedValueOnce({ tenants: [] })
      .mockResolvedValueOnce({ events: [] })
      .mockResolvedValueOnce(null);
    vi.mocked(getApiClient).mockReturnValue(createApiClientMock({ request }) as never);

    render(<AdminPage />);

    await waitFor(() => {
      expect(screen.getByText("No quota data available.")).toBeInTheDocument();
      expect(screen.getByText("0 Total")).toBeInTheDocument();
      expect(screen.getByText("fail")).toBeInTheDocument();
    });
  });

  it("renders error state when admin fetch fails", async () => {
    vi.mocked(getApiClient).mockReturnValue(createApiClientMock({
      request: vi.fn().mockRejectedValue(new Error("admin data failed")),
    }) as never);

    render(<AdminPage />);

    await waitFor(() => {
      expect(screen.getByText("admin data failed")).toBeInTheDocument();
    });
  });

  it("renders access denied for non-admin roles and skips requests", async () => {
    vi.mocked(useAuth).mockReturnValue(createAuthContextValue({
      isAuthenticated: true,
      account: {
        idTokenClaims: { roles: ["Agent.Developer"] },
      } as never,
    }) as never);
    const request = vi.fn();
    vi.mocked(getApiClient).mockReturnValue(createApiClientMock({ request }) as never);

    render(<AdminPage />);

    await waitFor(() => {
      expect(screen.getByText("Access Denied")).toBeInTheDocument();
      expect(screen.getByText("Platform operator role required.")).toBeInTheDocument();
    });
    expect(request).not.toHaveBeenCalled();
  });

  it("refreshes the dashboard on demand", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(healthOk)
      .mockResolvedValueOnce(richTenantRows)
      .mockResolvedValueOnce(quotaRows)
      .mockResolvedValueOnce({ tenants: [] })
      .mockResolvedValueOnce({ events: [] })
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce(healthOk)
      .mockResolvedValueOnce(richTenantRows)
      .mockResolvedValueOnce(quotaRows)
      .mockResolvedValueOnce({ tenants: [] })
      .mockResolvedValueOnce({ events: [] })
      .mockResolvedValueOnce(null);

    await renderAdminPageWithData(request);

    fireEvent.click(screen.getByRole("button", { name: /refresh data/i }));

    await waitFor(() => {
      expect(request).toHaveBeenCalledTimes(12);
    });
  });

  it("opens tenant details and exports audit data", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(healthOk)
      .mockResolvedValueOnce(richTenantRows)
      .mockResolvedValueOnce(quotaRows)
      .mockResolvedValueOnce({ tenants: [] })
      .mockResolvedValueOnce({ events: [] })
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce({ downloadUrl: "https://example.test/audit.csv" });

    await renderAdminPageWithData(request);

    fireEvent.click(screen.getAllByRole("button", { name: "View" })[0]);

    expect(await screen.findByText("Tenant Details")).toBeInTheDocument();
    expect(screen.getByText("123456789012")).toBeInTheDocument();
    expect(screen.getByText("$250.00")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /export invocation audit/i }));

    await waitFor(() => {
      expect(openMock).toHaveBeenCalledWith("https://example.test/audit.csv", "_blank", "noopener,noreferrer");
    });
    expect(notifyMock).toHaveBeenCalledWith(expect.objectContaining({
      title: "Export Started",
      severity: "success",
    }));

    fireEvent.click(screen.getByRole("button", { name: /close panel/i }));

    await waitFor(() => {
      expect(screen.queryByText("Tenant Details")).not.toBeInTheDocument();
    });
  });

  it("reports audit export failures without opening a tab", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(healthOk)
      .mockResolvedValueOnce(richTenantRows)
      .mockResolvedValueOnce(quotaRows)
      .mockResolvedValueOnce({ tenants: [] })
      .mockResolvedValueOnce({ events: [] })
      .mockResolvedValueOnce(null)
      .mockRejectedValueOnce(new Error("audit failed"));

    await renderAdminPageWithData(request);

    fireEvent.click(screen.getAllByRole("button", { name: "View" })[0]);
    expect(await screen.findByText("Tenant Details")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /export invocation audit/i }));

    await waitFor(() => {
      expect(notifyMock).toHaveBeenCalledWith(expect.objectContaining({
        title: "Export Failed",
        message: "audit failed",
        severity: "error",
      }));
    });
    expect(openMock).not.toHaveBeenCalled();
  });

  it("suspends the selected active tenant from the drawer", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(healthOk)
      .mockResolvedValueOnce(richTenantRows)
      .mockResolvedValueOnce(quotaRows)
      .mockResolvedValueOnce({ tenants: [] })
      .mockResolvedValueOnce({ events: [] })
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce({ tenantId: "t-001", status: "suspended" })
      .mockResolvedValueOnce(healthOk)
      .mockResolvedValueOnce({
        items: [
          { ...richTenantRows.items[0], status: "suspended" },
          richTenantRows.items[1],
        ],
      })
      .mockResolvedValueOnce(quotaRows)
      .mockResolvedValueOnce({ tenants: [] })
      .mockResolvedValueOnce({ events: [] })
      .mockResolvedValueOnce(null);

    await renderAdminPageWithData(request);

    fireEvent.click(screen.getAllByRole("button", { name: "View" })[0]);
    expect(await screen.findByText("Tenant Details")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /suspend tenant access/i }));

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith("/v1/tenants/t-001", expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ status: "suspended" }),
      }));
    });
    expect(notifyMock).toHaveBeenCalledWith(expect.objectContaining({
      title: "Tenant Updated",
      message: "Tenant t-001 is now suspended.",
      severity: "success",
    }));
  });

  it("renders populated operations panels and closes the drawer from the backdrop", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(healthOk)
      .mockResolvedValueOnce({
        items: [
          richTenantRows.items[0],
          { ...richTenantRows.items[1], status: "deleted" },
        ],
      })
      .mockResolvedValueOnce(quotaRows)
      .mockResolvedValueOnce({
        tenants: [
          { tenantId: "t-001", tokens: 12000 },
          { tenantId: "t-002", tokens: 8700 },
        ],
      })
      .mockResolvedValueOnce({
        events: [
          {
            tenantId: "t-001",
            timestamp: "2026-03-01T10:15:00Z",
            details: "WAF block spike detected",
          },
          {
            tenantId: "t-002",
            timestamp: "2026-03-01T10:18:00Z",
            details: "Auth anomaly escalated",
          },
        ],
      })
      .mockResolvedValueOnce({ errorRate: 0.08, threshold: 0.05 });

    vi.mocked(getApiClient).mockReturnValue(createApiClientMock({ request }) as never);

    render(<AdminPage initialSection="ops" />);

    expect(await screen.findByText("Operations / ops")).toBeInTheDocument();
    expect(screen.getByText("WAF block spike detected")).toBeInTheDocument();
    expect(screen.getByText("12,000 tokens")).toBeInTheDocument();
    expect(screen.getByText("8.0%")).toBeInTheDocument();

    const betaRow = screen.getByText("Beta").closest("tr");
    expect(betaRow).not.toBeNull();
    expect(within(betaRow as HTMLElement).queryByRole("button", { name: /reinstate/i })).not.toBeInTheDocument();

    fireEvent.click(screen.getAllByRole("button", { name: "View" })[0]);
    expect(await screen.findByText("Tenant Details")).toBeInTheDocument();

    const backdrop = screen.getByRole("dialog").querySelector(".bg-gray-500");
    expect(backdrop).not.toBeNull();
    fireEvent.click(backdrop as HTMLElement);

    await waitFor(() => {
      expect(screen.queryByText("Tenant Details")).not.toBeInTheDocument();
    });
  });

  it("updates tenant status successfully from the table and drawer", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(healthOk)
      .mockResolvedValueOnce(richTenantRows)
      .mockResolvedValueOnce(quotaRows)
      .mockResolvedValueOnce({ tenants: [] })
      .mockResolvedValueOnce({ events: [] })
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce({ tenantId: "t-001", status: "suspended" })
      .mockResolvedValueOnce(healthOk)
      .mockResolvedValueOnce(richTenantRows)
      .mockResolvedValueOnce(quotaRows)
      .mockResolvedValueOnce({ tenants: [] })
      .mockResolvedValueOnce({ events: [] })
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce({ tenantId: "t-002", status: "active" })
      .mockResolvedValueOnce(healthOk)
      .mockResolvedValueOnce({
        items: [
          { ...richTenantRows.items[0], status: "suspended" },
          { ...richTenantRows.items[1], status: "active" },
        ],
      })
      .mockResolvedValueOnce(quotaRows)
      .mockResolvedValueOnce({ tenants: [] })
      .mockResolvedValueOnce({ events: [] })
      .mockResolvedValueOnce(null);

    await renderAdminPageWithData(request);

    fireEvent.click(screen.getByRole("button", { name: "Suspend" }));

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith("/v1/tenants/t-001", expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ status: "suspended" }),
      }));
    });
    expect(notifyMock).toHaveBeenCalledWith(expect.objectContaining({
      title: "Tenant Updated",
      message: "Tenant t-001 is now suspended.",
    }));

    const betaRow = screen.getByText("Beta").closest("tr");
    expect(betaRow).not.toBeNull();
    fireEvent.click(within(betaRow as HTMLElement).getByRole("button", { name: "View" }));
    fireEvent.click(await screen.findByRole("button", { name: /reinstate tenant access/i }));

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith("/v1/tenants/t-002", expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ status: "active" }),
      }));
    });
    expect(notifyMock).toHaveBeenCalledWith(expect.objectContaining({
      message: "Tenant t-002 is now active.",
      severity: "success",
    }));
  });

  it("reports tenant update failures", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(healthOk)
      .mockResolvedValueOnce(richTenantRows)
      .mockResolvedValueOnce(quotaRows)
      .mockResolvedValueOnce({ tenants: [] })
      .mockResolvedValueOnce({ events: [] })
      .mockResolvedValueOnce(null)
      .mockRejectedValueOnce(new Error("tenant patch failed"));

    await renderAdminPageWithData(request);

    fireEvent.click(screen.getByRole("button", { name: "Suspend" }));

    await waitFor(() => {
      expect(notifyMock).toHaveBeenCalledWith(expect.objectContaining({
        title: "Update Failed",
        message: "tenant patch failed",
        severity: "error",
      }));
    });
  });

  it("shows failover as disabled in the ADR-023 topology", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(healthOk)
      .mockResolvedValueOnce(richTenantRows)
      .mockResolvedValueOnce(quotaRows)
      .mockResolvedValueOnce({ tenants: [] })
      .mockResolvedValueOnce({ events: [] })
      .mockResolvedValueOnce({ errorRate: 0.02, threshold: 0.05 });

    await renderAdminPageWithData(request);

    expect(screen.getByRole("button", { name: /failover disabled/i })).toBeDisabled();
    expect(request).not.toHaveBeenCalledWith("/v1/platform/failover", expect.anything());
    expect(confirmMock).not.toHaveBeenCalled();
  });
});
