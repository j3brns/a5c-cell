import { InteractionRequiredAuthError, type AccountInfo } from "@azure/msal-browser";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { acquireAccessToken, refreshAccessTokenWithBff } from "./AuthProvider";
import { ApiError, getApiClient } from "../api/client";

// Mock ApiClient
vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    getApiClient: vi.fn(),
  };
});

const account = {
  homeAccountId: "home-account",
  environment: "login.microsoftonline.com",
  tenantId: "tenant-id",
  username: "julia@example.com",
  localAccountId: "local-account",
  name: "Julia Example",
} satisfies AccountInfo;

describe("AuthProvider token acquisition helpers", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("returns silent token when acquireTokenSilent succeeds", async () => {
    const acquireTokenSilent = vi.fn().mockResolvedValue({ accessToken: "silent-token" });
    const acquireTokenPopup = vi.fn();

    const token = await acquireAccessToken({
      client: {
        acquireTokenSilent,
        acquireTokenPopup,
      } as never,
      account,
      scopes: ["api://platform-dev/Agent.Invoke"],
    });

    expect(token).toBe("silent-token");
    expect(acquireTokenSilent).toHaveBeenCalledTimes(1);
    expect(acquireTokenPopup).not.toHaveBeenCalled();
  });

  it("refreshes through the BFF using an MSAL assertion token", async () => {
    const acquireTokenSilent = vi.fn().mockResolvedValue({ accessToken: "assertion-token" });
    const acquireTokenPopup = vi.fn();
    const bffTokenRefresh = vi.fn().mockResolvedValue({ accessToken: "bff-token" });
    vi.mocked(getApiClient).mockReturnValue({ bffTokenRefresh } as never);

    const token = await refreshAccessTokenWithBff({
      client: {
        acquireTokenSilent,
        acquireTokenPopup,
      } as never,
      account,
      scopes: ["api://platform-dev/Agent.Invoke"],
    });

    expect(token).toBe("bff-token");
    expect(acquireTokenSilent).toHaveBeenCalledTimes(1);
    expect(bffTokenRefresh).toHaveBeenCalledWith(
      { scopes: ["api://platform-dev/Agent.Invoke"] },
      { accessToken: "assertion-token" },
    );
    expect(acquireTokenPopup).not.toHaveBeenCalled();
  });

  it("returns the interactive MSAL token when BFF refresh fails after interaction", async () => {
    const acquireTokenSilent = vi
      .fn()
      .mockRejectedValue(new InteractionRequiredAuthError("interaction_required", "login"));
    const acquireTokenPopup = vi.fn().mockResolvedValue({ accessToken: "popup-token" });
    const bffTokenRefresh = vi.fn().mockRejectedValue(new Error("bff down"));
    vi.mocked(getApiClient).mockReturnValue({ bffTokenRefresh } as never);
    const consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => undefined);

    const token = await refreshAccessTokenWithBff({
      client: {
        acquireTokenSilent,
        acquireTokenPopup,
      } as never,
      account,
      scopes: ["api://platform-dev/Agent.Invoke"],
      forceRefresh: true,
    });

    expect(token).toBe("popup-token");
    expect(acquireTokenSilent).toHaveBeenCalledWith({
      account,
      scopes: ["api://platform-dev/Agent.Invoke"],
      forceRefresh: true,
    });
    expect(acquireTokenPopup).toHaveBeenCalledWith({
      account,
      scopes: ["api://platform-dev/Agent.Invoke"],
    });
    expect(getApiClient).toHaveBeenCalledTimes(1);
    expect(consoleErrorSpy).toHaveBeenCalledWith("[Auth] BFF OBO refresh failed (Error)");
  });

  it("sanitizes BFF refresh failures so assertion details do not reach the console", async () => {
    const acquireTokenSilent = vi.fn().mockResolvedValue({ accessToken: "assertion-token" });
    const acquireTokenPopup = vi.fn();
    const response = new Response(
      JSON.stringify({
        error: {
          code: "UPSTREAM_FAILURE",
          message: "assertion-token should not be logged",
        },
      }),
      { status: 503, headers: { "content-type": "application/json" } },
    );
    const bffTokenRefresh = vi
      .fn()
      .mockRejectedValue(
        new ApiError("popup-token should not be logged", response, {
          error: {
            code: "UPSTREAM_FAILURE",
            message: "assertion-token should not be logged",
          },
        }),
      );
    vi.mocked(getApiClient).mockReturnValue({ bffTokenRefresh } as never);
    const consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => undefined);

    await expect(
      refreshAccessTokenWithBff({
        client: {
          acquireTokenSilent,
          acquireTokenPopup,
        } as never,
        account,
        scopes: ["api://platform-dev/Agent.Invoke"],
      }),
    ).rejects.toBeInstanceOf(ApiError);

    expect(consoleErrorSpy).toHaveBeenCalledWith(
      "[Auth] BFF OBO refresh failed (ApiError(status=503,code=UPSTREAM_FAILURE))",
    );
    expect(consoleErrorSpy.mock.calls[0]?.[0]).not.toContain("assertion-token");
    expect(consoleErrorSpy.mock.calls[0]?.[0]).not.toContain("popup-token");
  });

  it("rethrows non-interaction errors during normal token acquisition", async () => {
    const acquireTokenSilent = vi.fn().mockRejectedValue(new Error("network down"));
    const acquireTokenPopup = vi.fn();

    await expect(
      acquireAccessToken({
        client: {
          acquireTokenSilent,
          acquireTokenPopup,
        } as never,
        account,
        scopes: ["api://platform-dev/Agent.Invoke"],
      }),
    ).rejects.toThrow("network down");

    expect(acquireTokenPopup).not.toHaveBeenCalled();
  });
});
