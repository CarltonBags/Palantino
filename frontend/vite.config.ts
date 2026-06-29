import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev proxy: forward /api/* to the FastAPI backend so the SPA can call it
// same-origin without CORS config.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
