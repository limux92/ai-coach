import { defineConfig } from 'vite';
export default defineConfig({
  base: '/dashboard/',
  build: { outDir: '../adapters/mcp/static/dashboard', emptyOutDir: true, sourcemap: false },
  server: { port: 5174, strictPort: true },
});
