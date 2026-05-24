import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  testMatch: '**/*.test.ts',
  webServer: {
    command: 'npm run dev',
    port: 5173,
    timeout: 120 * 1000,
    reuseExistingServer: !process.env.CI,
    ignoreHTTPSErrors: true,
  },
  use: {
    baseURL: process.env.BASE_URL || 'http://localhost:5173',
    ignoreHTTPSErrors: true,
    trace: 'on-first-retry',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
