<script setup lang="ts">
import { computed } from 'vue'
import type { SubTaskRecord } from '../../../utils/api/masterAgent'

const props = defineProps<{ subtask: SubTaskRecord }>()

const statusColor = computed(() => {
  switch (props.subtask.status) {
    case 'done':
      return '#4caf50'
    case 'running':
      return '#2196f3'
    case 'failed':
      return '#f44336'
    case 'awaiting_approval':
      return '#ff9800'
    case 'aborted':
      return '#9e9e9e'
    default:
      return '#bdbdbd'
  }
})

const brandLabel = computed(() => props.subtask.agent_brand || '?')
</script>

<template>
  <div class="subtask-card">
    <div class="row">
      <span class="brand">[{{ brandLabel }}]</span>
      <span class="status" :style="{ background: statusColor }">{{ subtask.status }}</span>
    </div>
    <div class="session">session: {{ subtask.session_id || '—' }}</div>
    <div v-if="subtask.cwd" class="cwd">cwd: {{ subtask.cwd }}</div>
    <div v-if="subtask.last_event_type" class="last-event">last: {{ subtask.last_event_type }}</div>
    <div v-if="subtask.final_text" class="final-text">{{ subtask.final_text }}</div>
    <div v-if="subtask.error" class="error">{{ subtask.error }}</div>
  </div>
</template>

<style scoped>
.subtask-card {
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 8px;
  background: #fafafa;
  font-size: 13px;
  line-height: 1.5;
}
.row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}
.brand {
  font-weight: 600;
  color: #555;
}
.status {
  color: #fff;
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 10px;
  text-transform: uppercase;
}
.session,
.cwd,
.last-event {
  color: #666;
  font-size: 12px;
}
.final-text {
  margin-top: 6px;
  padding: 6px;
  background: #fff;
  border-left: 3px solid #4caf50;
  white-space: pre-wrap;
  word-break: break-word;
}
.error {
  margin-top: 6px;
  padding: 6px;
  background: #ffebee;
  border-left: 3px solid #f44336;
  white-space: pre-wrap;
}
</style>
