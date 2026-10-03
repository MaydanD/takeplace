/// <reference types="vitest/config" />
import { fileURLToPath, URL } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// The dev server proxies the API so the browser talks to a single origin, which
// matches production (frontend and API share an origin behind Caddy).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "TAKEPLACE_");
  const proxyTarget =
    env.TAKEPLACE_FRONTEND_PROXY_TARGET ??
    env.TAKEPLACE_API_BASE_URL ??
    "http://127.0.0.1:8000";

  return {
    plugins: [react()],
    resolve: {
      alias: {
        "@": fileURLToPath(new URL("./src", import.meta.url)),
      },
    },
    server: {
      port: Number(env.TAKEPLACE_FRONTEND_PORT ?? 5173),
      strictPort: true,
      proxy: {
        "/api": { target: proxyTarget, changeOrigin: true },
        "/health": { target: proxyTarget, changeOrigin: true },
        "/openapi.json": { target: proxyTarget, changeOrigin: true },
      },
    },
    preview: {
      port: Number(env.TAKEPLACE_FRONTEND_PORT ?? 5173),
      strictPort: true,
    },
    test: {
      globals: true,
      environment: "jsdom",
      setupFiles: ["./src/test/setup.ts"],
      css: false,
      include: ["src/**/*.{test,spec}.{ts,tsx}"],
    },
  };
});
