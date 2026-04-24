import { ApiError } from "../api/client";

type AuthLogLevel = "info" | "warn" | "error";

const AUTH_LOGGING_ENABLED = Boolean(import.meta.env?.DEV || import.meta.env?.MODE === "test");

export function summarizeAuthError(error: unknown): string {
  if (error instanceof ApiError) {
    const details = [`status=${error.status}`];
    if (error.code) {
      details.push(`code=${error.code}`);
    }
    return `${error.name}(${details.join(",")})`;
  }

  if (error instanceof Error) {
    return error.name || "Error";
  }

  return "UnknownError";
}

export function logAuthMessage(level: AuthLogLevel, message: string): void {
  if (!AUTH_LOGGING_ENABLED) {
    return;
  }
  console[level](message);
}

export function logAuthError(message: string, error: unknown): void {
  logAuthMessage("error", `${message} (${summarizeAuthError(error)})`);
}
