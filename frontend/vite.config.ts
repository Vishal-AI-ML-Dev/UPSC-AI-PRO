import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"

// Vite config. Dev server proxies /api to the backend when VITE_API_BASE is a
// relative path; for a full URL (Render) the app calls it directly with CORS.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
})
