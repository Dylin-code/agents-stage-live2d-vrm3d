<script setup lang="ts">
import { computed } from 'vue'
import type { SubTaskRecord } from '../../../utils/api/masterAgent'
import SubTaskCard from './SubTaskCard.vue'

const props = defineProps<{ subtasks: Record<string, SubTaskRecord> }>()

const ordered = computed(() =>
  Object.values(props.subtasks).sort((a, b) => a.created_at - b.created_at),
)
</script>

<template>
  <aside class="subtask-list">
    <h3>子任務 ({{ ordered.length }})</h3>
    <div v-if="ordered.length === 0" class="empty">尚未派發任務</div>
    <SubTaskCard v-for="task in ordered" :key="task.id" :subtask="task" />
  </aside>
</template>

<style scoped>
.subtask-list {
  flex: 0 0 320px;
  padding: 12px;
  background: #f5f5f5;
  border-left: 1px solid #e0e0e0;
  overflow-y: auto;
}
h3 {
  margin: 0 0 12px;
  font-size: 14px;
  color: #424242;
}
.empty {
  color: #9e9e9e;
  font-size: 13px;
  text-align: center;
  margin-top: 20px;
}
</style>
