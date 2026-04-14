import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'path'

function parsePort(value: string | undefined, fallback: number): number {
  const parsed = Number.parseInt(value || '', 10)
  return Number.isFinite(parsed) ? parsed : fallback
}

export default defineConfig(({ mode }) => {
  const repoRoot = path.resolve(__dirname, '..')
  const env = loadEnv(mode, repoRoot, '')
  const backendHost = env.VITE_BACKEND_HOST || '127.0.0.1'
  const backendPort = parsePort(env.VITE_BACKEND_PORT, 8000)
  const frontendHost = env.VITE_FRONTEND_HOST || '0.0.0.0'
  const frontendPort = parsePort(env.VITE_FRONTEND_PORT, 5173)

  return {
    envDir: repoRoot,
    base: './',
    plugins: [vue()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, 'src'),
      },
    },
    build: {
      rollupOptions: {
        output: {
          manualChunks: undefined,
        },
      },
    },
    server: {
      host: frontendHost,
      port: frontendPort,
      fs: {
        allow: ['..'],
      },
    },
  }
})
