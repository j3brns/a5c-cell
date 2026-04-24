import { useCallback } from "react";
import { logAuthError } from "../auth/logging";
import { defaultScopes } from "../auth/msalConfig";
import { useAuth } from "../auth/useAuth";

export function useBffTokenRefresh() {
  const { refreshAccessTokenViaBff } = useAuth();

  const refresh = useCallback(async (scopes: string[] = defaultScopes) => {
    try {
      const accessToken = await refreshAccessTokenViaBff(scopes);
      return { accessToken };
    } catch (err) {
      logAuthError("[BFF] Token refresh failed", err);
      throw err;
    }
  }, [refreshAccessTokenViaBff]);

  return { refresh };
}
