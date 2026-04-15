import { create } from 'zustand'

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  tool_calls?: ToolCall[]
  artifacts?: object[]
  token_count?: number
  created_at?: string
  isStreaming?: boolean
  streamBuffer?: string
  streamingStatus?: string  // heartbeat status: 'thinking' | 'tool:query_database' | etc.
  startedAt?: number   // ms timestamp when streaming began
  duration?: number    // ms elapsed from start to finish (set on finalize)
}

export interface ToolCall {
  tool_use_id: string
  tool_name: string
  input?: object
  output?: unknown
  status: 'running' | 'done' | 'error'
  startedAt?: number    // ms timestamp when tool started
  completedAt?: number  // ms timestamp when tool finished
}

export interface Conversation {
  id: string
  title: string
  model_id?: string
  status: string
  created_at: string
  updated_at: string
}

interface ChatState {
  conversations: Conversation[]
  activeConversationId: string | null
  messages: Record<string, Message[]>
  streamingMessageId: string | null
  ws: WebSocket | null

  setConversations: (convs: Conversation[]) => void
  addConversation: (conv: Conversation) => void
  removeConversation: (id: string) => void
  updateConversationTitle: (id: string, title: string) => void
  setActiveConversation: (id: string | null) => void

  setMessages: (convId: string, msgs: Message[]) => void
  appendMessage: (convId: string, msg: Message) => void
  updateStreamBuffer: (convId: string, msgId: string, delta: string) => void
  updateStreamingStatus: (convId: string, msgId: string, status: string) => void
  finalizeMessage: (convId: string, msgId: string, content: string) => void
  addToolCall: (convId: string, msgId: string, toolCall: ToolCall) => void
  updateToolCall: (convId: string, msgId: string, toolUseId: string, update: Partial<ToolCall>) => void

  setWs: (ws: WebSocket | null) => void
  setStreamingMessageId: (id: string | null) => void
}

export const useChatStore = create<ChatState>((set, get) => ({
  conversations: [],
  activeConversationId: null,
  messages: {},
  streamingMessageId: null,
  ws: null,

  setConversations: (convs) => set({ conversations: convs }),
  addConversation: (conv) =>
    set((s) => ({ conversations: [conv, ...s.conversations] })),
  removeConversation: (id) =>
    set((s) => ({ conversations: s.conversations.filter((c) => c.id !== id) })),
  updateConversationTitle: (id, title) =>
    set((s) => ({
      conversations: s.conversations.map((c) => (c.id === id ? { ...c, title } : c)),
    })),
  setActiveConversation: (id) => set({ activeConversationId: id }),

  setMessages: (convId, msgs) =>
    set((s) => ({ messages: { ...s.messages, [convId]: msgs } })),
  appendMessage: (convId, msg) =>
    set((s) => ({
      messages: { ...s.messages, [convId]: [...(s.messages[convId] || []), msg] },
    })),
  updateStreamBuffer: (convId, msgId, delta) =>
    set((s) => ({
      messages: {
        ...s.messages,
        [convId]: (s.messages[convId] || []).map((m) =>
          m.id === msgId
            ? { ...m, streamBuffer: (m.streamBuffer || '') + delta, isStreaming: true }
            : m
        ),
      },
    })),
  updateStreamingStatus: (convId, msgId, status) =>
    set((s) => ({
      messages: {
        ...s.messages,
        [convId]: (s.messages[convId] || []).map((m) =>
          m.id === msgId ? { ...m, streamingStatus: status } : m
        ),
      },
    })),
  finalizeMessage: (convId, msgId, content) =>
    set((s) => ({
      messages: {
        ...s.messages,
        [convId]: (s.messages[convId] || []).map((m) =>
          m.id === msgId
            ? {
                ...m,
                content,
                streamBuffer: undefined,
                isStreaming: false,
                duration: m.startedAt ? Date.now() - m.startedAt : undefined,
              }
            : m
        ),
      },
    })),
  addToolCall: (convId, msgId, toolCall) =>
    set((s) => ({
      messages: {
        ...s.messages,
        [convId]: (s.messages[convId] || []).map((m) =>
          m.id === msgId
            ? { ...m, tool_calls: [...(m.tool_calls || []), toolCall] }
            : m
        ),
      },
    })),
  updateToolCall: (convId, msgId, toolUseId, update) =>
    set((s) => ({
      messages: {
        ...s.messages,
        [convId]: (s.messages[convId] || []).map((m) =>
          m.id === msgId
            ? {
                ...m,
                tool_calls: (m.tool_calls || []).map((tc) =>
                  tc.tool_use_id === toolUseId ? { ...tc, ...update } : tc
                ),
              }
            : m
        ),
      },
    })),

  setWs: (ws) => set({ ws }),
  setStreamingMessageId: (id) => set({ streamingMessageId: id }),
}))
