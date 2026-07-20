import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { resolveLocalApiBase } from "./src/api/localApiBase";

export default defineConfig(({ command, mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const configuredBackendTarget = (env.VITE_DEV_API_TARGET ?? "").trim();
  if (command === "serve" && mode !== "test" && !configuredBackendTarget) {
    throw new Error(
      "VITE_DEV_API_TARGET is required; choose the backend instance for this worktree explicitly",
    );
  }
  const backendTarget = configuredBackendTarget
    ? resolveLocalApiBase(configuredBackendTarget)
    : null;

  return {
    base: "/workbench-assets/",
    plugins: [react()],
    server: {
      host: "127.0.0.1",
      port: 5174,
      strictPort: false,
      ...(backendTarget
        ? {
            proxy: {
              "/v2": {
                target: backendTarget,
                changeOrigin: true,
              },
              "/providers": {
                target: backendTarget,
                changeOrigin: true,
              },
              "/settings": {
                target: backendTarget,
                changeOrigin: true,
              },
              "/health": {
                target: backendTarget,
                changeOrigin: true,
              },
              "/metrics": {
                target: backendTarget,
                changeOrigin: true,
              },
              "/desktop": {
                target: backendTarget,
                changeOrigin: true,
              },
              "/live/asr/stream/ws": {
                target: backendTarget,
                changeOrigin: true,
                ws: true,
              },
            },
          }
        : {}),
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
