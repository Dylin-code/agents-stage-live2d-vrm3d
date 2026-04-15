import { defineConfig, loadEnv, type Plugin } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'path'

/** Attach error handlers to every incoming socket so ECONNRESET won't crash the dev server. */
function socketErrorGuard(): Plugin {
  return {
    name: 'socket-error-guard',
    configureServer(server) {
      server.httpServer?.on('connection', (socket) => {
        socket.on('error', () => {})
      })
    },
  }
}

function parsePort(value: string | undefined, fallback: number): number {
  const parsed = Number.parseInt(value || '', 10)
  return Number.isFinite(parsed) ? parsed : fallback
}

/** Parse comma-separated host list. Supports the special value "all" to disable host check. */
function parseAllowedHosts(value: string | undefined): true | string[] | undefined {
  if (!value) return undefined
  const trimmed = value.trim()
  if (!trimmed) return undefined
  if (trimmed === 'all' || trimmed === '*') return true
  return trimmed
    .split(',')
    .map((h) => h.trim())
    .filter(Boolean)
}

export default defineConfig(({ mode }) => {
  const repoRoot = path.resolve(__dirname, '..')
  const env = loadEnv(mode, repoRoot, '')
  const backendHost = env.VITE_BACKEND_HOST || '127.0.0.1'
  const backendPort = parsePort(env.VITE_BACKEND_PORT, 8000)
  const frontendHost = env.VITE_FRONTEND_HOST || '0.0.0.0'
  const frontendPort = parsePort(env.VITE_FRONTEND_PORT, 5173)
  const allowedHosts = parseAllowedHosts(env.VITE_ALLOWED_HOSTS)

  return {
    envDir: repoRoot,
    base: './',
    plugins: [vue(), socketErrorGuard()],
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
      ...(allowedHosts !== undefined ? { allowedHosts } : {}),
      fs: {
        allow: ['..'],
      },
      proxy: {
        '/api': {
          target: `http://${backendHost}:${backendPort}`,
          changeOrigin: true,
          ws: true,
        },
      },
    },
  }
})
