<template>
  <main class="desktop-widget-shell">
    <div class="desktop-widget-controls">
      <button type="button" title="重新載入" @click="reloadWindow">↻</button>
      <button type="button" title="關閉" @click="closeWindow">×</button>
    </div>

    <section class="desktop-widget-stage" :class="statusClass">
      <DesktopWidgetLive2D :state="monitor.activeState.value" />
      <div class="status-bubble">
        <span class="status-dot"></span>
        <span>{{ monitor.activeStateText.value }}</span>
      </div>
    </section>

    <footer class="desktop-widget-status">
      <div class="session-row">
        <span class="brand">{{ monitor.brandName.value }}</span>
        <span class="session-name">{{ sessionName }}</span>
      </div>
      <div class="meta-row">
        <span>{{ monitor.cwdLabel.value }}</span>
        <span v-if="monitor.rateLimitText.value">{{ monitor.rateLimitText.value }}</span>
        <span v-if="monitor.lastEventText.value">{{ monitor.lastEventText.value }}</span>
      </div>
    </footer>
  </main>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import DesktopWidgetLive2D from '../components/desktop-widget/DesktopWidgetLive2D.vue'
import { useDesktopWidgetMonitor } from './desktop-widget/desktopWidgetMonitor'

const monitor = useDesktopWidgetMonitor()

const sessionName = computed(() => monitor.activeSession.value?.display_name || 'Bridge monitor')
const statusClass = computed(() => ({
  'is-disconnected': monitor.connectionStatus.value === 'disconnected',
  'is-connected': monitor.connectionStatus.value === 'connected',
}))

function closeWindow(): void {
  window.desktopWidget?.close()
}

function reloadWindow(): void {
  window.desktopWidget?.reload()
}
</script>

<style scoped>
.desktop-widget-shell {
  position: relative;
  display: grid;
  grid-template-rows: minmax(0, 1fr) auto;
  width: 100vw;
  height: 100vh;
  overflow: hidden;
  color: #f8fbff;
  background: transparent;
  user-select: none;
  -webkit-app-region: drag;
}

.desktop-widget-controls {
  position: absolute;
  top: 12px;
  right: 12px;
  z-index: 20;
  display: flex;
  gap: 6px;
  opacity: 0;
  transition: opacity 160ms ease;
  -webkit-app-region: no-drag;
}

.desktop-widget-shell:hover .desktop-widget-controls {
  opacity: 1;
}

.desktop-widget-controls button {
  display: grid;
  width: 28px;
  height: 28px;
  place-items: center;
  border: 1px solid rgb(255 255 255 / 28%);
  border-radius: 999px;
  color: #f8fbff;
  font-size: 17px;
  line-height: 1;
  background: rgb(12 18 28 / 72%);
  box-shadow: 0 8px 24px rgb(0 0 0 / 18%);
  cursor: pointer;
}

.desktop-widget-stage {
  position: relative;
  min-height: 0;
  padding: 20px 18px 0;
}

.desktop-widget-stage::after {
  position: absolute;
  right: 52px;
  bottom: 18px;
  left: 52px;
  height: 18px;
  content: "";
  background: radial-gradient(ellipse at center, rgb(12 18 28 / 36%), rgb(12 18 28 / 0) 68%);
  pointer-events: none;
}

.desktop-widget-live2d {
  width: 100%;
  height: 100%;
}

.status-bubble {
  position: absolute;
  top: 54px;
  left: 22px;
  display: inline-flex;
  max-width: calc(100% - 44px);
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border: 1px solid rgb(255 255 255 / 24%);
  border-radius: 8px;
  color: #f8fbff;
  font-size: 13px;
  font-weight: 700;
  line-height: 1.2;
  background: rgb(20 28 40 / 74%);
  box-shadow: 0 12px 28px rgb(0 0 0 / 18%);
  backdrop-filter: blur(10px);
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: #74d680;
  box-shadow: 0 0 14px rgb(116 214 128 / 70%);
}

.is-disconnected .status-dot {
  background: #ff6b72;
  box-shadow: 0 0 14px rgb(255 107 114 / 72%);
}

.desktop-widget-status {
  display: grid;
  gap: 5px;
  padding: 10px 14px 16px;
  border-top: 1px solid rgb(255 255 255 / 14%);
  background: linear-gradient(180deg, rgb(14 19 28 / 38%), rgb(14 19 28 / 78%));
  backdrop-filter: blur(12px);
}

.session-row,
.meta-row {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 8px;
}

.brand {
  flex: 0 0 auto;
  padding: 3px 7px;
  border-radius: 6px;
  color: #0f1722;
  font-size: 11px;
  font-weight: 800;
  background: #d8f3ff;
}

.session-name {
  min-width: 0;
  overflow: hidden;
  font-size: 13px;
  font-weight: 800;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.meta-row {
  overflow: hidden;
  color: #d7e2f0;
  font-size: 11px;
}

.meta-row span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
