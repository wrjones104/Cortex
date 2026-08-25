import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["favicon.svg"],
      manifest: {
        name: "Cortex",
        short_name: "Cortex",
        description: "A local-first AI knowledge vault.",
        start_url: "/capture",
        share_target: {
          action: "/capture",
          method: "GET",
          params: { title: "title", text: "text", url: "url" },
        },
        shortcuts: [
          { name: "Capture a note", url: "/capture" },
          { name: "Ask the vault", url: "/chat" },
        ],
        scope: "/",
        display: "standalone",
        orientation: "portrait",
        background_color: "#0e1213",
        theme_color: "#0e6c6d",
        icons: [
          { src: "icon-192.png", sizes: "192x192", type: "image/png" },
          { src: "icon-512.png", sizes: "512x512", type: "image/png" },
          { src: "icon-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
        ],
      },
      workbox: {
        // Cache the app shell so it opens instantly and works with no signal.
        // API responses are deliberately NOT cached: a stale note is worse
        // than an honest error, and the offline queue (M6) is what makes
        // capture work without a connection.
        globPatterns: ["**/*.{js,css,html,svg,png,woff2}"],
        navigateFallback: "index.html",
        navigateFallbackDenylist: [/^\/api/, /^\/health/],
        cleanupOutdatedCaches: true,
      },
      devOptions: { enabled: false },
    }),
  ],
  server: {
    port: 5173,
    // Dev-only convenience: same-origin requests to the API avoid CORS while
    // iterating. Production builds talk to whatever address setup stored.
    proxy: {
      "/api": { target: "http://127.0.0.1:8765", changeOrigin: true },
      "/health": { target: "http://127.0.0.1:8765", changeOrigin: true },
    },
  },
  build: { outDir: "dist", sourcemap: false },
});
