import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// /api is proxied to FastAPI so API keys never touch the browser.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8000" } },
});
