<template>
  <Modal
    :open="visible"
    title="選擇工作目錄"
    :confirm-loading="loading"
    ok-text="使用目前資料夾"
    cancel-text="取消"
    :ok-button-props="{ disabled: !currentPath || loading }"
    @ok="handleConfirm"
    @cancel="handleClose"
  >
    <div class="directory-browser-modal">
      <div class="directory-browser-toolbar">
        <button
          type="button"
          class="directory-browser-action"
          :disabled="loading || !parentPath"
          @click="void openDirectory(parentPath || '')"
        >
          上一層
        </button>
        <button
          type="button"
          class="directory-browser-action"
          :disabled="loading"
          @click="void reloadCurrentDirectory()"
        >
          重新整理
        </button>
      </div>

      <div class="directory-browser-current" :title="currentPath || '根目錄'">
        {{ currentPath || '根目錄' }}
      </div>

      <input
        v-model.trim="searchKeyword"
        type="text"
        class="directory-browser-search"
        placeholder="快速搜尋目前目錄下的資料夾"
      >

      <div v-if="ancestors.length" class="directory-browser-breadcrumbs">
        <button
          v-for="ancestor in ancestors"
          :key="ancestor.path"
          type="button"
          class="directory-browser-crumb"
          :class="{ active: ancestor.path === currentPath }"
          @click="void openDirectory(ancestor.path)"
        >
          {{ ancestor.name }}
        </button>
      </div>

      <div v-if="errorMessage" class="directory-browser-error">
        {{ errorMessage }}
      </div>

      <div class="directory-browser-list">
        <div v-if="loading" class="directory-browser-placeholder">
          讀取目錄中...
        </div>
        <div v-else-if="!directories.length" class="directory-browser-placeholder">
          目前目錄沒有可瀏覽的子資料夾
        </div>
        <div v-else-if="!filteredDirectories.length" class="directory-browser-placeholder">
          找不到符合搜尋條件的資料夾
        </div>
        <button
          v-for="entry in filteredDirectories"
          :key="entry.path"
          type="button"
          class="directory-browser-entry"
          @click="void openDirectory(entry.path)"
        >
          <span class="directory-browser-entry-name">{{ entry.name }}</span>
          <span class="directory-browser-entry-path">{{ entry.path }}</span>
        </button>
      </div>
    </div>
  </Modal>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'

import {
  fetchSessionBridgeDirectories,
  type SessionBridgeDirectoryBrowseResponse,
} from '../../utils/api/sessionBridge'
import { filterDirectoryEntries } from './directoryBrowserFilter'

interface Props {
  visible: boolean
  initialPath?: string
  serverUrl?: string
}

const props = defineProps<Props>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  select: [path: string]
}>()

const loading = ref(false)
const currentPath = ref('')
const parentPath = ref<string | null>(null)
const directories = ref<SessionBridgeDirectoryBrowseResponse['directories']>([])
const ancestors = ref<SessionBridgeDirectoryBrowseResponse['ancestors']>([])
const errorMessage = ref('')
const searchKeyword = ref('')

const filteredDirectories = computed(() => filterDirectoryEntries(directories.value, searchKeyword.value))

async function loadDirectory(path?: string): Promise<void> {
  loading.value = true
  errorMessage.value = ''
  try {
    const response = await fetchSessionBridgeDirectories(props.serverUrl, path)
    currentPath.value = response.current_path || ''
    parentPath.value = response.parent_path
    directories.value = response.directories
    ancestors.value = response.ancestors
    searchKeyword.value = ''
  } catch (error) {
    const detail = String((error as Error)?.message || error || 'unknown error')
    errorMessage.value = `目錄讀取失敗：${detail}`
    directories.value = []
    ancestors.value = []
  } finally {
    loading.value = false
  }
}

async function openDirectory(path: string): Promise<void> {
  await loadDirectory(path)
}

async function reloadCurrentDirectory(): Promise<void> {
  await loadDirectory(currentPath.value)
}

function handleClose(): void {
  emit('update:visible', false)
}

function handleConfirm(): void {
  const selected = currentPath.value.trim()
  if (!selected) {
    message.warning('請先進入要使用的資料夾')
    return
  }
  emit('select', selected)
  emit('update:visible', false)
}

watch(
  () => props.visible,
  (visible) => {
    if (!visible) return
    void loadDirectory(props.initialPath || '')
  },
)
</script>

<style scoped>
.directory-browser-modal {
  display: grid;
  gap: 10px;
}

.directory-browser-toolbar {
  display: flex;
  gap: 8px;
}

.directory-browser-action {
  border: 1px solid rgba(182, 212, 248, 0.28);
  border-radius: 8px;
  background: rgba(7, 17, 30, 0.9);
  color: #e8f2ff;
  font-size: 12px;
  padding: 6px 10px;
  cursor: pointer;
}

.directory-browser-action:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.directory-browser-current {
  border-radius: 10px;
  background: rgba(8, 19, 33, 0.78);
  color: #eef6ff;
  font-size: 12px;
  line-height: 1.5;
  padding: 10px 12px;
  word-break: break-all;
}

.directory-browser-search {
  width: 100%;
  border: 1px solid rgba(182, 212, 248, 0.22);
  border-radius: 10px;
  background: rgba(7, 17, 30, 0.9);
  color: #eef6ff;
  font-size: 12px;
  padding: 9px 12px;
}

.directory-browser-breadcrumbs {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.directory-browser-crumb {
  border: 1px solid rgba(182, 212, 248, 0.22);
  border-radius: 999px;
  background: rgba(10, 23, 39, 0.88);
  color: rgba(232, 242, 255, 0.86);
  font-size: 12px;
  padding: 4px 10px;
  cursor: pointer;
}

.directory-browser-crumb.active {
  border-color: rgba(143, 214, 255, 0.55);
  color: #ffffff;
}

.directory-browser-error {
  border: 1px solid rgba(255, 157, 157, 0.26);
  border-radius: 10px;
  background: rgba(67, 19, 19, 0.5);
  color: #ffd7d7;
  font-size: 12px;
  padding: 10px 12px;
}

.directory-browser-list {
  display: grid;
  gap: 6px;
  max-height: 320px;
  overflow: auto;
}

.directory-browser-placeholder {
  border: 1px dashed rgba(182, 212, 248, 0.24);
  border-radius: 10px;
  color: rgba(220, 234, 248, 0.72);
  font-size: 12px;
  padding: 18px 12px;
  text-align: center;
}

.directory-browser-entry {
  width: 100%;
  border: 1px solid rgba(182, 212, 248, 0.18);
  border-radius: 10px;
  background: rgba(9, 21, 36, 0.88);
  color: #eff7ff;
  cursor: pointer;
  display: grid;
  gap: 2px;
  padding: 10px 12px;
  text-align: left;
}

.directory-browser-entry:hover {
  border-color: rgba(143, 214, 255, 0.45);
  background: rgba(12, 27, 44, 0.94);
}

.directory-browser-entry-name {
  font-size: 13px;
  font-weight: 600;
}

.directory-browser-entry-path {
  color: rgba(214, 229, 244, 0.7);
  font-size: 11px;
  word-break: break-all;
}
</style>
