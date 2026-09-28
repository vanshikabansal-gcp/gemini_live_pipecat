import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

/**
 * Abhay negotiation challenge: a separate build into dist/challenge.
 *
 * Kept apart from the Voice Studio bundle so the challenge service can serve
 * only this output. The studio bundle embeds every persona prompt, including
 * the one that states Abhay's floor price.
 *
 * Local dev: `npm run dev:challenge`, then open /challenge.html.
 */
const backend = "http://127.0.0.1:7860";

export default defineConfig({
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  server: {
    allowedHosts: true,
    proxy: {
      "/connect": { target: backend, changeOrigin: true },
      "/api": { target: backend, changeOrigin: true },
      "/ws": { target: backend.replace("http", "ws"), ws: true, changeOrigin: true },
    },
  },
  build: {
    outDir: "dist/challenge",
    emptyOutDir: true,
    rollupOptions: { input: fileURLToPath(new URL("./challenge.html", import.meta.url)) },
  },
  plugins: [react()],
});
