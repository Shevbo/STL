import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import path from 'path';

export default defineConfig(({ mode }) => ({
  plugins: [svelte()],
  resolve: {
    alias: { $lib: path.resolve('./src/lib') },
    // В тестах Svelte иначе резолвится в СЕРВЕРНУЮ сборку, где onMount и $effect
    // недоступны (lifecycle_function_unavailable): смонтировать компонент нельзя.
    // Условие только для теста — боевая сборка остаётся как была.
    conditions: mode === 'test' ? ['browser'] : undefined,
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/ws': { target: 'ws://localhost:8000', ws: true }
    }
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['src/test-setup.ts']
  }
}));
