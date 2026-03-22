<template>
  <div class="chat-container" :class="{ 'chat-container--danger': isFullAccess, 'transparent-mode': props.transparentMode }">
    <div class="chat-header">
      <div class="chat-header-title">
        <span v-if="!isEditing">{{ localConversation.label }}</span>
        <Input
          v-if="isEditing"
          class="chat-header-title-input"
          v-model:value="localConversation.label"
          @focusout="handleSaveConversationLabel"
          @keydown.enter="handleSaveConversationLabel"
        />
      </div>
      <div class="chat-header-actions">
        <EditOutlined @click="handleEditConversationLabel" ref="chatHeaderTitleInputRef" />
      </div>
    </div>

    <div v-if="props.forceAgentSession" class="agent-settings-wrapper">
      <button class="agent-settings-toggle" type="button" @click="agentSettingsExpanded = !agentSettingsExpanded">
        {{ agentSettingsExpanded ? '收起設定 ▲' : '模型設定 ▼' }}
      </button>
      <div v-show="agentSettingsExpanded" class="agent-session-controls">
        <div class="agent-session-row">
          <label>Model</label>
          <select v-model="agentOptions.model" @change="emitAgentOptions">
            <option value="">預設 ({{ agentOptions.agent_brand === 'claude' ? 'sonnet' : 'gpt-5.3-codex' }})</option>
            <option v-for="m in availableModels" :key="m" :value="m">{{ m }}</option>
          </select>

          <label>推理</label>
          <select v-model="agentOptions.reasoning_effort" @change="emitAgentOptions">
            <option value="">預設 (medium)</option>
            <option value="low">low</option>
            <option value="medium">medium</option>
            <option value="high">high</option>
          </select>

          <label>執行模式</label>
          <select v-model="agentOptions.permission_mode" @change="emitAgentOptions">
            <option value="default">預設 (自動接受編輯)</option>
            <option value="full">完整存取權</option>
          </select>
        </div>

        <div class="agent-session-row">
          <label>分支</label>
          <select v-model="agentOptions.git_branch" @change="emitAgentOptions">
            <option value="">(不切換)</option>
            <option v-for="b in availableBranches" :key="b" :value="b">{{ b }}</option>
          </select>
          <button class="mini-btn" type="button" @click="requestRefreshBranches">刷新分支</button>

          <label class="plan-toggle">
            <input type="checkbox" v-model="agentOptions.plan_mode" @change="emitAgentOptions">
            計劃模式
          </label>

          <label>CWD 覆寫</label>
          <input
            class="cwd-input"
            type="text"
            v-model.trim="agentOptions.cwd_override"
            placeholder="預設沿用該 session"
            @change="emitAgentOptions"
          >
        </div>

        <div class="agent-session-row">
          <label>圖片</label>
          <input ref="imageInputRef" type="file" accept="image/*" multiple @change="handleImageInputChange">
          <span class="hint">支援貼上圖片 (Ctrl/Cmd+V)</span>
        </div>
        <div v-if="agentImages.length > 0" class="image-chips">
          <span class="image-chip" v-for="(img, idx) in agentImages" :key="img.name + idx">
            {{ img.name }}
            <button type="button" @click="removeImage(idx)">x</button>
          </span>
        </div>
      </div>
    </div>

    <div v-if="isFullAccess && props.forceAgentSession" class="danger-banner">
      ⚠️ 完整存取權已啟用：Agent 可讀寫系統任意路徑、執行任意指令，請確認你信任此 session 的操作範圍。
    </div>

    <div class="messages" ref="messagesRef">
      <Flex gap="middle" vertical>
        <BubbleList :roles="roles" :items="message2BubbleListItem()" />
        <div v-if="isMessageLoading" class="loading-indicator">
          <div class="loading-content">
            <div class="loading-dots">
              <span></span>
              <span></span>
              <span></span>
            </div>
            <span class="loading-text">正在思考...</span>
          </div>
        </div>
      </Flex>
    </div>

    <div v-if="pendingApproval" class="approval-box">
      <div class="approval-title">需要權限確認</div>
      <div class="approval-cmd">{{ pendingApproval.command || '（未提供命令）' }}</div>
      <div class="approval-why">{{ pendingApproval.justification || '未提供 justification' }}</div>
      <div class="approval-actions">
        <button type="button" @click="handleApproval('allow_once')">允許一次</button>
        <button type="button" class="danger" @click="handleApproval('deny_once')">拒絕一次</button>
        <button type="button" @click="handleApproval('allow_prefix')">永久允許前綴</button>
      </div>
    </div>

    <div class="chat-input-container">
      <Sender
        class="chat-sender"
        @submit="sendMessage"
        v-model:value="value"
        :footer="renderFooter"
        :placeholder="getInputPlaceholder()"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { Flex, Input, Typography } from 'ant-design-vue'
import { Sender, BubbleList } from 'ant-design-x-vue'
import type { BubbleListProps } from 'ant-design-x-vue'
import { UserOutlined, EditOutlined, AndroidOutlined, SearchOutlined, QuestionCircleOutlined, BookOutlined } from '@ant-design/icons-vue'
import { h, nextTick, onMounted, onUnmounted, ref, toRef, watch, computed } from 'vue'
import { PropType } from 'vue'
import { fetchEventData } from 'fetch-sse'
import markdownit from 'markdown-it'
import { v4 as uuidv4 } from 'uuid'
import { Message, SystemSettings, ChatHistory, Conversation } from '../types/message'
import {
  buildChatRequestPayload,
  AgentImageInput,
  AgentSessionPayloadOptions,
  resolveChatRequestMode,
  resolveChatRequestPath,
} from '../utils/chatRequestPath'
import { DEFAULT_AGENT_BRANDS, getAgentBrandModels } from '../utils/agentBrands'
import { submitAgentApproval } from '../utils/api/sessionBridge'
import ToolCall from './tool_call.vue'

const md = markdownit({ html: true, breaks: true })
const isEditing = ref(false)
const chatHeaderTitleInputRef = ref<HTMLInputElement>()
const messagesRef = ref<HTMLDivElement>()
const imageInputRef = ref<HTMLInputElement>()
const isAgent = ref(false)
const webSearch = ref(false)
const useRAG = ref(false)

interface PendingApproval {
  pending_id: string
  command: string
  justification: string
  suggested_prefix: string[]
}

interface AgentSessionUiOptions {
  model?: string
  reasoning_effort?: string
  permission_mode?: string
  plan_mode?: boolean
  cwd_override?: string
  git_branch?: string
  available_models?: string[]
  available_branches?: string[]
  cwd?: string
  agent_brand?: string
}

const roles: BubbleListProps['roles'] = {
  assistant: {
    placement: 'start',
    avatar: { icon: h(UserOutlined), style: { background: '#fde3cf' } },
    messageRender: (content) =>
      h(Typography, null, {
        default: () => h('div', { innerHTML: md.render(content) }),
      }),
  },
  waiting_for_input: {
    placement: 'start',
    avatar: { icon: h(QuestionCircleOutlined), style: { background: '#fde3cf' } },
    messageRender: (content) =>
      h(Typography, null, {
        default: () => h('div', { innerHTML: md.render(content) }),
      }),
  },
  tool: {
    placement: 'start',
    avatar: { icon: h(UserOutlined), style: { background: '#fde3cf' } },
    messageRender: (content) => {
      const toolCall = JSON.parse(content)[0]
      if (toolCall.name.startsWith('transfer_to_')) {
        return h(Typography, null, {
          default: () => h('div', { innerHTML: md.render(`移交控制權給\`${toolCall.name.replace('transfer_to_', '')}\``) }),
        })
      }
      return h(ToolCall, {
        function_name: toolCall.name,
        function_args: toolCall.arguments,
        response: toolCall.response,
      })
    },
  },
  user: {
    placement: 'end',
    avatar: { icon: h(UserOutlined), style: { background: '#fde3cf' } },
  },
}

const props = defineProps({
  conversation: {
    type: Object as PropType<Conversation>,
    required: true,
  },
  onNewMessage: {
    type: Function as PropType<(conversation: Conversation) => void>,
    required: true,
  },
  systemSettings: {
    type: Object as PropType<SystemSettings>,
    required: false,
  },
  forceAgentic: {
    type: Boolean,
    default: false,
  },
  forceAgentSession: {
    type: Boolean,
    default: false,
  },
  agentSessionOptions: {
    type: Object as PropType<AgentSessionUiOptions>,
    required: false,
    default: () => ({}),
  },
  onAgentSessionOptionsChange: {
    type: Function as PropType<(options: AgentSessionUiOptions) => void>,
    required: false,
  },
  onRequestRefreshBranches: {
    type: Function as PropType<() => void>,
    required: false,
  },
  transparentMode: {
    type: Boolean,
    default: false,
  },
  defaultAgentSettingsExpanded: {
    type: Boolean,
    default: true,
  },
})

const value = ref('')
const agentSettingsExpanded = ref(props.defaultAgentSettingsExpanded)
const localConversation = ref<Conversation>(props.conversation)
const conversationRef = toRef(props, 'conversation')
const waiting_for_input = ref(false)
const isMessageLoading = ref(false)
const agentOptions = ref<AgentSessionUiOptions>({})
const agentImages = ref<AgentImageInput[]>([])

const isFullAccess = computed(() => agentOptions.value.permission_mode === 'full')
const pendingApproval = ref<PendingApproval | null>(null)

const availableModels = ref<string[]>([])
const availableBranches = ref<string[]>([])

const message2BubbleListItem = () => {
  return localConversation.value.messages.map((msg) => ({
    key: msg.id,
    role: msg.role,
    content: msg.content,
    timestamp: msg.timestamp,
    loading: msg.loading,
  }))
}

const renderFooter = () => {
  if (props.forceAgentic || props.forceAgentSession) {
    return h(Flex, { align: 'start', gap: 'middle' }, [])
  }
  return h(Flex, { align: 'start', gap: 'middle' }, [
    h(AndroidOutlined, {
      onClick: () => {
        isAgent.value = !isAgent.value
        if (isAgent.value) {
          useRAG.value = false
          webSearch.value = false
        }
      },
      style: {
        color: isAgent.value ? '#1890ff' : '#999',
        fontSize: '20px',
      },
    }),
    h(SearchOutlined, {
      onClick: () => {
        webSearch.value = !webSearch.value
        if (webSearch.value) {
          useRAG.value = false
          isAgent.value = false
        }
      },
      style: {
        color: webSearch.value ? '#1890ff' : '#999',
        fontSize: '20px',
      },
    }),
    h(BookOutlined, {
      onClick: () => {
        useRAG.value = !useRAG.value
        if (useRAG.value) {
          isAgent.value = false
          webSearch.value = false
        }
      },
      style: {
        color: useRAG.value ? '#1890ff' : '#999',
        fontSize: '20px',
      },
    }),
  ])
}

const getInputPlaceholder = () => {
  if (pendingApproval.value) {
    return '請先回覆上方權限請求...'
  }
  if (waiting_for_input.value) {
    return '請回答上面的问题...'
  }
  if (props.forceAgentSession) {
    const brand = agentOptions.value.agent_brand || 'codex'
    if (brand === 'claude') {
      return '請輸入訊息，我會透過本地 Claude Session 繼續對話...'
    }
    return '請輸入訊息，我會透過本地 Agent Session 繼續對話...'
  }
  if (props.forceAgentic) {
    return '请输入您的任务，我會以 Agent 模式繼續此 Session...'
  }
  if (isAgent.value) {
    return '请输入您的任务，我会调用相应的工具来帮助您...'
  }
  return `与 ${props.systemSettings?.assistantSettings.assistantName || 'AI助手'} 对话...`
}

const syncAgentOptions = () => {
  const source = props.agentSessionOptions || {}
  const brand = (source.agent_brand || 'codex').toLowerCase()
  agentOptions.value = {
    model: source.model || '',
    reasoning_effort: source.reasoning_effort || '',
    permission_mode: source.permission_mode || 'default',
    plan_mode: !!source.plan_mode,
    cwd_override: source.cwd_override || '',
    git_branch: source.git_branch || '',
    available_models: source.available_models || [],
    available_branches: source.available_branches || [],
    cwd: source.cwd || '',
    agent_brand: brand,
  }
  const fallbackModels = getAgentBrandModels(DEFAULT_AGENT_BRANDS, brand)
  availableModels.value = (source.available_models && source.available_models.length > 0)
    ? source.available_models
    : fallbackModels
  availableBranches.value = source.available_branches || []
}

const emitAgentOptions = () => {
  if (!props.onAgentSessionOptionsChange) return
  props.onAgentSessionOptionsChange({
    ...agentOptions.value,
    available_models: availableModels.value,
    available_branches: availableBranches.value,
  })
}

const requestRefreshBranches = () => {
  props.onRequestRefreshBranches?.()
}

const removeImage = (idx: number) => {
  agentImages.value.splice(idx, 1)
}

const readFileAsDataUrl = (file: File): Promise<string> => {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || ''))
    reader.onerror = reject
    reader.readAsDataURL(file)
  })
}

const appendFiles = async (files: File[]) => {
  for (const file of files) {
    if (!file.type.startsWith('image/')) continue
    try {
      const dataUrl = await readFileAsDataUrl(file)
      agentImages.value.push({
        name: file.name,
        data_url: dataUrl,
      })
    } catch {
      // ignore single file read failure
    }
  }
}

const handleImageInputChange = async (event: Event) => {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files || [])
  await appendFiles(files)
  if (input) {
    input.value = ''
  }
}

const handlePaste = async (event: ClipboardEvent) => {
  if (!props.forceAgentSession) return
  const files = Array.from(event.clipboardData?.files || [])
  if (files.length === 0) return
  await appendFiles(files)
}

const handleMessageChange = (newVal: Message[]) => {
  if (localConversation.value.key === '') {
    localConversation.value.messages = newVal
    localConversation.value.key = uuidv4().toString()
    localConversation.value.label = newVal[0].content
    localConversation.value.updatedAt = new Date().getTime()
    localConversation.value.createdAt = new Date().getTime()
  } else if (localConversation.value.label === '' || localConversation.value.label === '新对话') {
    localConversation.value.label = newVal[0].content
    localConversation.value.updatedAt = new Date().getTime()
    localConversation.value.createdAt = new Date().getTime()
  }
  props.onNewMessage(localConversation.value)
}

const scrollToBottom = async () => {
  await nextTick()
  if (!messagesRef.value) return
  messagesRef.value.scrollTo({
    top: messagesRef.value.scrollHeight,
    behavior: 'smooth',
  })
}

watch(conversationRef, async () => {
  localConversation.value = props.conversation
  await scrollToBottom()
})

watch(() => localConversation.value.messages.length, async () => {
  await scrollToBottom()
})

watch(() => props.agentSessionOptions, () => {
  syncAgentOptions()
}, { deep: true, immediate: true })

const sendMessage = async () => {
  if (pendingApproval.value) {
    localConversation.value.messages.push({
      id: localConversation.value.messages.length + 1,
      role: 'assistant',
      content: '目前有待確認的權限請求，請先點擊上方按鈕允許或拒絕。',
      timestamp: new Date().toLocaleString(),
      loading: false,
    })
    localConversation.value.messages = [...localConversation.value.messages]
    props.onNewMessage(localConversation.value)
    return
  }

  const message = value.value
  value.value = ''
  if (!message) return

  localConversation.value.messages.push({
    id: localConversation.value.messages.length + 1,
    role: 'user',
    content: message,
    timestamp: new Date().toLocaleString(),
    loading: false,
  })
  handleMessageChange(localConversation.value.messages)
  const chatHistory = localConversation.value.messages.map((msg) => ({
    role: msg.role === 'user' ? 'user' : 'assistant',
    content: msg.content,
  }))
  await sendMessageToServer(chatHistory)
}

const handleApproval = async (decision: 'allow_once' | 'deny_once' | 'allow_prefix') => {
  if (!pendingApproval.value || !props.systemSettings) return
  try {
    const prefixRule = decision === 'allow_prefix'
      ? (pendingApproval.value.suggested_prefix && pendingApproval.value.suggested_prefix.length > 0
          ? pendingApproval.value.suggested_prefix
          : (pendingApproval.value.command || '').split(/\s+/).filter(Boolean).slice(0, 2))
      : undefined
    await submitAgentApproval(props.systemSettings.serverUrl, {
      pending_id: pendingApproval.value.pending_id,
      decision,
      prefix_rule: prefixRule,
      agent_brand: agentOptions.value.agent_brand,
    })
    localConversation.value.messages.push({
      id: localConversation.value.messages.length + 1,
      role: 'assistant',
      content: decision === 'deny_once' ? '已拒絕本次權限請求。' : '已送出權限允許，等待執行結果。',
      timestamp: new Date().toLocaleString(),
      loading: false,
    })
    localConversation.value.messages = [...localConversation.value.messages]
    props.onNewMessage(localConversation.value)
    pendingApproval.value = null
  } catch (error) {
    localConversation.value.messages.push({
      id: localConversation.value.messages.length + 1,
      role: 'assistant',
      content: `回覆權限失敗：${String((error as Error)?.message || error || 'unknown error')}`,
      timestamp: new Date().toLocaleString(),
      loading: false,
    })
    localConversation.value.messages = [...localConversation.value.messages]
    props.onNewMessage(localConversation.value)
  }
}

const sendMessageToServer = async (messages: ChatHistory[]) => {
  if (props.forceAgentic || props.forceAgentSession) {
    isAgent.value = props.forceAgentic
    webSearch.value = false
    useRAG.value = false
  }

  isMessageLoading.value = true
  waiting_for_input.value = false

  const waitingMessage = {
    id: localConversation.value.messages.length + 1,
    role: 'assistant',
    content: '',
    timestamp: new Date().toLocaleString(),
    loading: true,
  }
  localConversation.value.messages.push(waitingMessage)

  let waitingRemoved = false
  let isThinking = false
  let thinking = ''

  const requestMode = resolveChatRequestMode(!!props.forceAgentSession, !!props.forceAgentic, isAgent.value)
  if (props.systemSettings?.assistantSettings.sysPrompt && requestMode !== 'agent_session') {
    messages.unshift({
      role: 'system',
      content: props.systemSettings.assistantSettings.sysPrompt,
    })
  }

  const reqPath = resolveChatRequestPath(requestMode)
  if (localConversation.value.key === '') {
    localConversation.value.key = uuidv4().toString()
  }

  const agentPayload: AgentSessionPayloadOptions | undefined = props.forceAgentSession
    ? {
        images: [...agentImages.value],
        model: agentOptions.value.model || undefined,
        reasoning_effort: agentOptions.value.reasoning_effort || undefined,
        permission_mode: agentOptions.value.permission_mode || 'default',
        plan_mode: !!agentOptions.value.plan_mode,
        cwd_override: agentOptions.value.cwd_override || undefined,
        git_branch: agentOptions.value.git_branch || undefined,
        agent_brand: agentOptions.value.agent_brand || undefined,
      }
    : undefined

  const payload = buildChatRequestPayload(requestMode, {
    model: props.systemSettings!.assistantSettings.model,
    messages: messages.map((msg) => ({ role: msg.role, content: msg.content })),
    agents: JSON.parse(props.systemSettings!.assistantSettings.agents || '[]'),
    chatId: localConversation.value.key,
    isResume: localConversation.value.messages[localConversation.value.messages.length - 1].role === 'waiting_for_input',
    webSearch: props.forceAgentic || props.forceAgentSession ? false : webSearch.value,
    rag: props.forceAgentic || props.forceAgentSession ? false : useRAG.value,
    agentOptions: agentPayload,
  })

  // 每次訊息使用當前附件，一次送出後清空
  agentImages.value = []

  const ensureAssistantMessage = () => {
    let lastMessage = localConversation.value.messages[localConversation.value.messages.length - 1]
    if (!lastMessage || lastMessage.role !== 'assistant') {
      lastMessage = {
        id: localConversation.value.messages.length + 1,
        role: 'assistant',
        content: '',
        timestamp: new Date().toLocaleString(),
        loading: false,
      }
      localConversation.value.messages.push(lastMessage)
    }
    return lastMessage
  }

  await fetchEventData(props.systemSettings!.serverUrl + reqPath, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    data: payload,
    onMessage: (event) => {
      const data = JSON.parse(event!.data)
      if (data.type === 'text' && data.content !== '') {
        if (!waitingRemoved) {
          localConversation.value.messages.pop()
          waitingRemoved = true
          isMessageLoading.value = false
          localConversation.value.messages.push({
            id: localConversation.value.messages.length + 1,
            role: 'assistant',
            content: '',
            timestamp: new Date().toLocaleString(),
            loading: false,
          })
        }
        const lastMessage = ensureAssistantMessage()
        if (data.content === '<think>' || isThinking) {
          if (data.content === '</think>') {
            isThinking = false
            return
          }
          isThinking = true
          thinking = (thinking + data.content).replace('<think>', '').replace('</think>', '')
          lastMessage.content = thinking
        } else {
          lastMessage.content += data.content
        }
        localConversation.value.messages = [...localConversation.value.messages]
        return
      }

      if (data.type === 'tool_calls' && data.content !== '') {
        if (!waitingRemoved) {
          localConversation.value.messages.pop()
          waitingRemoved = true
          isMessageLoading.value = false
        }
        if (Array.isArray(data.content) && data.content.some((c: any) => c.name === 'request_user_input')) {
          const tc = data.content.find((c: any) => c.name === 'request_user_input')
          if (tc) {
            localConversation.value.messages.push({
              id: localConversation.value.messages.length + 1,
              role: 'waiting_for_input',
              content: tc.arguments?.prompt || '請回答問題後再繼續。',
              timestamp: new Date().toLocaleString(),
              loading: false,
            })
            waiting_for_input.value = true
            data.content = data.content.filter((c: any) => c.name !== 'request_user_input')
          }
        }
        if (Array.isArray(data.content) && data.content.length > 0) {
          localConversation.value.messages.push({
            id: localConversation.value.messages.length + 1,
            role: 'tool',
            content: JSON.stringify(data.content),
            timestamp: new Date().toLocaleString(),
            loading: false,
          })
          localConversation.value.messages = [...localConversation.value.messages]
        }
        return
      }

      if (data.type === 'context' && data.content && props.forceAgentSession) {
        const ctx = data.content || {}
        if (ctx.model) agentOptions.value.model = String(ctx.model)
        if (ctx.effort) agentOptions.value.reasoning_effort = String(ctx.effort)
        if (ctx.permission_mode) {
          agentOptions.value.permission_mode = String(ctx.permission_mode)
        } else if (ctx.sandbox_mode) {
          agentOptions.value.permission_mode = String(ctx.sandbox_mode) === 'danger-full-access' ? 'full' : 'default'
        }
        if (ctx.cwd) agentOptions.value.cwd_override = String(ctx.cwd)
        if (typeof ctx.plan_mode === 'boolean') agentOptions.value.plan_mode = !!ctx.plan_mode
        if (ctx.agent_brand) agentOptions.value.agent_brand = String(ctx.agent_brand)
        emitAgentOptions()
        return
      }

      if (data.type === 'approval_request' && data.content) {
        pendingApproval.value = {
          pending_id: String(data.content.pending_id || ''),
          command: String(data.content.command || ''),
          justification: String(data.content.justification || ''),
          suggested_prefix: Array.isArray(data.content.suggested_prefix)
            ? data.content.suggested_prefix.map((x: any) => String(x))
            : [],
        }
        if (!waitingRemoved) {
          localConversation.value.messages.pop()
          waitingRemoved = true
          isMessageLoading.value = false
        }
        localConversation.value.messages.push({
          id: localConversation.value.messages.length + 1,
          role: 'assistant',
          content: `收到權限請求：\n\`${pendingApproval.value.command || 'unknown command'}\``,
          timestamp: new Date().toLocaleString(),
          loading: false,
        })
        localConversation.value.messages = [...localConversation.value.messages]
        props.onNewMessage(localConversation.value)
        return
      }

      if (data.type === 'error') {
        if (!waitingRemoved) {
          localConversation.value.messages.pop()
          waitingRemoved = true
        }
        isMessageLoading.value = false
        const lastMessage = ensureAssistantMessage()
        lastMessage.content = String(data.content || '發生未知錯誤')
        localConversation.value.messages = [...localConversation.value.messages]
        return
      }

      if (data.type === 'done') {
        if (!waitingRemoved) {
          localConversation.value.messages.pop()
          waitingRemoved = true
          localConversation.value.messages.push({
            id: localConversation.value.messages.length + 1,
            role: 'assistant',
            content: '已完成，但未收到可顯示的文字回覆。',
            timestamp: new Date().toLocaleString(),
            loading: false,
          })
          localConversation.value.messages = [...localConversation.value.messages]
        }
        isMessageLoading.value = false
      }
    },
  })
    .then(() => {
      isMessageLoading.value = false
      if (!waitingRemoved) {
        localConversation.value.messages.pop()
        waitingRemoved = true
        localConversation.value.messages.push({
          id: localConversation.value.messages.length + 1,
          role: 'assistant',
          content: '已完成，但回覆內容為空。',
          timestamp: new Date().toLocaleString(),
          loading: false,
        })
        localConversation.value.messages = [...localConversation.value.messages]
      }
      localConversation.value.updatedAt = new Date().getTime()
      if (localConversation.value.label === '新对话') {
        localConversation.value.label = localConversation.value.messages[0].content as string
      }
      props.onNewMessage(localConversation.value)
    })
    .catch((error) => {
      isMessageLoading.value = false
      if (!waitingRemoved) {
        localConversation.value.messages.pop()
      }
      localConversation.value.messages.push({
        id: localConversation.value.messages.length + 1,
        role: 'assistant',
        content: `連線失敗：${String((error as Error)?.message || error || 'unknown error')}`,
        timestamp: new Date().toLocaleString(),
        loading: false,
      })
      localConversation.value.messages = [...localConversation.value.messages]
      props.onNewMessage(localConversation.value)
    })
}

const handleEditConversationLabel = () => {
  isEditing.value = true
  nextTick(() => {
    chatHeaderTitleInputRef.value?.focus()
  })
}

const handleSaveConversationLabel = () => {
  isEditing.value = false
  props.onNewMessage(localConversation.value)
}

onMounted(() => {
  document.addEventListener('paste', handlePaste)
})

onUnmounted(() => {
  document.removeEventListener('paste', handlePaste)
})
</script>

<style scoped>
.chat-container {
  width: 100%;
  height: 100%;
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
  background: linear-gradient(to bottom, rgba(255, 255, 255, 0), rgba(245, 245, 245, 0));
  border: 2px solid transparent;
  border-radius: 4px;
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}

.chat-container--danger {
  border-color: #ff4d4f;
  box-shadow: 0 0 0 2px rgba(255, 77, 79, 0.15);
}

.danger-banner {
  margin: 0 12px 4px;
  padding: 8px 12px;
  border-radius: 8px;
  background: rgba(255, 77, 79, 0.08);
  border: 1px solid rgba(255, 77, 79, 0.4);
  color: #cf1322;
  font-size: 12px;
  line-height: 1.5;
}

.chat-header {
  height: 56px;
  margin: 12px 12px 8px;
  border-radius: 12px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
  background-color: rgba(255, 255, 255, 0.8);
  display: flex;
  align-items: center;
  justify-content: center;
}

.chat-header-title {
  font-size: 16px;
  font-weight: 500;
  color: #333;
  text-align: center;
}

.chat-header-actions {
  margin-left: 10px;
  margin-right: 10px;
  cursor: pointer;
  color: #666;
}

.chat-header-title-input {
  width: 100%;
  height: 100%;
  border-radius: 8px;
  border: 1px solid #e0e0e0;
  padding: 4px 8px;
}

.agent-session-controls {
  margin: 0 12px 8px;
  padding: 10px;
  border-radius: 12px;
  background: rgba(18, 36, 56, 0.48);
  border: 1px solid rgba(166, 203, 245, 0.35);
  backdrop-filter: blur(6px);
}

.agent-session-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}

.agent-session-row:last-child {
  margin-bottom: 0;
}

.agent-session-row label {
  color: #e6f1ff;
  font-size: 12px;
  font-weight: 600;
}

.agent-session-row select,
.agent-session-row input[type='text'],
.agent-session-row input[type='file'] {
  background: rgba(7, 15, 26, 0.7);
  color: #eef6ff;
  border: 1px solid rgba(170, 208, 248, 0.28);
  border-radius: 8px;
  padding: 4px 8px;
  font-size: 12px;
}

.cwd-input {
  min-width: 240px;
}

.plan-toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.mini-btn {
  background: rgba(22, 56, 92, 0.76);
  color: #eaf3ff;
  border: 1px solid rgba(175, 210, 248, 0.34);
  border-radius: 8px;
  padding: 4px 8px;
  font-size: 12px;
  cursor: pointer;
}

.hint {
  color: rgba(221, 236, 255, 0.78);
  font-size: 11px;
}

.image-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.image-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 8px;
  border-radius: 999px;
  background: rgba(11, 25, 41, 0.8);
  border: 1px solid rgba(180, 213, 247, 0.28);
  color: #e9f3ff;
  font-size: 11px;
}

.image-chip button {
  border: none;
  background: transparent;
  color: #e9f3ff;
  cursor: pointer;
}

.messages {
  flex: 1;
  min-height: 0;
  width: 100%;
  height: 0;
  padding: 14px 9%;
  overflow-y: auto;
  overflow-x: hidden;
  scrollbar-width: thin;
}

.messages::-webkit-scrollbar {
  width: 5px;
}

.messages::-webkit-scrollbar-thumb {
  background-color: rgba(0, 0, 0, 0.2);
  border-radius: 10px;
}

.messages::-webkit-scrollbar-track {
  background-color: rgba(0, 0, 0, 0.05);
  border-radius: 10px;
}

.chat-input-container {
  width: 100%;
  padding: 16px;
  flex-shrink: 0;
  background: linear-gradient(to top, rgba(255, 255, 255, 0.9), transparent);
}

.chat-sender {
  align-self: center;
  margin-bottom: 0;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
  border-radius: 16px;
  background-color: #fff;
}

.loading-indicator {
  display: flex;
  justify-content: center;
  align-items: center;
  padding: 16px;
}

.loading-content {
  text-align: center;
  max-width: 600px;
}

.loading-dots {
  display: flex;
  gap: 4px;
  justify-content: center;
}

.loading-dots span {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background-color: #1890ff;
  animation: blink 1.4s infinite both;
}

.loading-dots span:nth-child(1) { animation-delay: -0.32s; }
.loading-dots span:nth-child(2) { animation-delay: -0.16s; }
.loading-dots span:nth-child(3) { animation-delay: 0s; }

.loading-text {
  font-size: 16px;
  font-weight: 500;
  color: #333;
  margin-top: 16px;
}

.approval-box {
  margin: 0 12px 8px;
  padding: 10px;
  border-radius: 12px;
  background: rgba(51, 25, 20, 0.74);
  border: 1px solid rgba(255, 176, 160, 0.5);
  color: #ffe9e1;
}

.approval-title {
  font-size: 13px;
  font-weight: 700;
}

.approval-cmd {
  margin-top: 6px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  word-break: break-all;
}

.approval-why {
  margin-top: 4px;
  font-size: 12px;
  opacity: 0.9;
}

.approval-actions {
  margin-top: 8px;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.approval-actions button {
  border: 1px solid rgba(255, 210, 200, 0.45);
  background: rgba(64, 34, 28, 0.72);
  color: #fff3ef;
  border-radius: 8px;
  padding: 4px 10px;
  cursor: pointer;
  font-size: 12px;
}

.approval-actions button.danger {
  border-color: rgba(255, 145, 130, 0.7);
  background: rgba(119, 34, 24, 0.82);
}

@keyframes blink {
  0%, 80%, 100% { transform: scale(0.5); }
  40% { transform: scale(1); }
}

/* ===== Agent settings collapsible toggle ===== */
.agent-settings-wrapper {
  margin: 0 12px 8px;
}

.agent-settings-toggle {
  width: 100%;
  padding: 6px 10px;
  border-radius: 8px;
  border: 1px solid rgba(166, 203, 245, 0.35);
  background: rgba(18, 36, 56, 0.48);
  backdrop-filter: blur(6px);
  color: #d9e9ff;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  text-align: left;
}

.agent-settings-toggle:hover {
  background: rgba(24, 46, 72, 0.6);
}

/* ===== Transparent mode (portrait mobile) ===== */
.chat-container.transparent-mode {
  background: transparent;
}

.chat-container.transparent-mode .chat-header {
  background-color: rgba(9, 23, 41, 0.4);
  backdrop-filter: blur(8px);
  color: #ecf5ff;
  box-shadow: none;
}

.chat-container.transparent-mode .chat-header-title span {
  color: #ecf5ff;
}

.chat-container.transparent-mode .messages {
  background: transparent;
}

.chat-container.transparent-mode :deep(.ant-bubble-content) {
  background: rgba(9, 23, 41, 0.5) !important;
  backdrop-filter: blur(6px);
  color: #f0f6ff !important;
}

.chat-container.transparent-mode :deep(.ant-bubble-content-inner) {
  color: #f0f6ff !important;
}

.chat-container.transparent-mode :deep(.ant-bubble-content .ant-typography),
.chat-container.transparent-mode :deep(.ant-bubble-content .ant-typography h1),
.chat-container.transparent-mode :deep(.ant-bubble-content .ant-typography h2),
.chat-container.transparent-mode :deep(.ant-bubble-content .ant-typography h3),
.chat-container.transparent-mode :deep(.ant-bubble-content .ant-typography h4),
.chat-container.transparent-mode :deep(.ant-bubble-content .ant-typography h5),
.chat-container.transparent-mode :deep(.ant-bubble-content .ant-typography h6),
.chat-container.transparent-mode :deep(.ant-bubble-content .ant-typography p),
.chat-container.transparent-mode :deep(.ant-bubble-content .ant-typography li),
.chat-container.transparent-mode :deep(.ant-bubble-content .ant-typography strong),
.chat-container.transparent-mode :deep(.ant-bubble-content .ant-typography em),
.chat-container.transparent-mode :deep(.ant-bubble-content .ant-typography blockquote),
.chat-container.transparent-mode :deep(.ant-bubble-content .ant-typography th),
.chat-container.transparent-mode :deep(.ant-bubble-content .ant-typography td) {
  color: #f0f6ff !important;
}

.chat-container.transparent-mode :deep(.ant-bubble-content .ant-typography a) {
  color: #8ec8ff !important;
}

.chat-container.transparent-mode :deep(.ant-bubble-content .ant-typography code) {
  color: #c8e6ff !important;
  background: rgba(255, 255, 255, 0.1) !important;
}

.chat-container.transparent-mode :deep(.ant-bubble-content .ant-typography pre) {
  background: rgba(0, 0, 0, 0.3) !important;
  color: #e8f0ff !important;
}

.chat-container.transparent-mode :deep(.ant-bubble-content .ant-typography pre code) {
  color: #e8f0ff !important;
  background: transparent !important;
}

/* Catch-all: ensure every element inside bubble content is light-colored in transparent mode */
.chat-container.transparent-mode :deep(.ant-bubble-content *) {
  color: #f0f6ff !important;
}

.chat-container.transparent-mode :deep(.ant-bubble-content a),
.chat-container.transparent-mode :deep(.ant-bubble-content .ant-typography a) {
  color: #8ec8ff !important;
}

.chat-container.transparent-mode :deep(.ant-bubble-content code) {
  color: #c8e6ff !important;
}

.chat-container.transparent-mode :deep(.ant-bubble-content pre),
.chat-container.transparent-mode :deep(.ant-bubble-content pre code) {
  color: #e8f0ff !important;
}

.chat-container.transparent-mode .agent-settings-wrapper .agent-settings-toggle {
  background: rgba(9, 23, 41, 0.4);
}

.chat-container.transparent-mode .agent-session-controls {
  background: rgba(9, 23, 41, 0.4);
}

.chat-container.transparent-mode .chat-input-container {
  background: rgba(9, 23, 41, 0.4);
  backdrop-filter: blur(8px);
  border-radius: 12px;
  margin: 0 4px 4px;
}

.chat-container.transparent-mode :deep(.chat-sender) {
  background-color: rgba(9, 23, 41, 0.5) !important;
  color: #f0f6ff !important;
}

.chat-container.transparent-mode :deep(.chat-sender textarea) {
  color: #f0f6ff !important;
}

.chat-container.transparent-mode :deep(.chat-sender textarea::placeholder) {
  color: rgba(220, 235, 255, 0.5) !important;
}
</style>
