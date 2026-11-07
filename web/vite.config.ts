import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  build: { outDir: "../visualstore/static", emptyOutDir: true },
  server: { proxy: { "/api": "http://127.0.0.1:8080" } },
  test: { environment: "node", include: ["src/**/*.test.ts", "src/**/*.test.tsx"] },
});
