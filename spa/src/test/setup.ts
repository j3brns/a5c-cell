import { vi } from 'vitest';

// Define required environment variables for tests using vi.stubEnv
// This ensures they are correctly populated in import.meta.env
vi.stubEnv('VITE_API_BASE_URL', 'https://api.example.test');
vi.stubEnv('VITE_ENTRA_CLIENT_ID', 'test-client-id');
vi.stubEnv('VITE_ENTRA_TENANT_ID', 'test-tenant-id');
vi.stubEnv('VITE_ENTRA_AUTHORITY', 'https://login.microsoftonline.com/test-tenant-id');
vi.stubEnv('VITE_ENTRA_SCOPES', 'api://platform-dev/Agent.Invoke');
vi.stubEnv('VITE_ENTRA_REDIRECT_URI', 'http://localhost:3000');
vi.stubEnv('VITE_ENTRA_POST_LOGOUT_REDIRECT_URI', 'http://localhost:3000');

// Mock useAuth globally to default to authenticated
vi.mock('../auth/useAuth', () => ({
  useAuth: () => ({
    isAuthenticated: true,
    getAccessToken: async () => 'token',
    user: { name: 'Test User', email: 'test@example.com' },
    login: vi.fn(),
    logout: vi.fn(),
  }),
}));
