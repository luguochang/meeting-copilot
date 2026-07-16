import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const backendTarget = env.VITE_DEV_API_TARGET || "http://127.0.0.1:8767";

  return {
    base: "/workbench-assets/",
    plugins: [react()],
    server: {
      host: "127.0.0.1",
      port: 5174,
      strictPort: false,
      proxy: {
        "/v2": {
          target: backendTarget,
          changeOrigin: true,
        },
        "/live/asr/stream/ws": {
          target: backendTarget,
          changeOrigin: true,
          ws: true,
        },
      },
    },
    test: {
      environment: "jsdom",
      setupFiles: "./src/test/setup.ts",
      globals: true,
      css: true,
      restoreMocks: true,
    },
  };
});
