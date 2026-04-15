import { useCallback, useEffect, useRef } from 'react'
import { useChatStore } from '../stores/chatStore'
import { useResultsStore } from '../stores/resultsStore'
import { useAuthStore } from '../stores/authStore'

const WS_BASE = import.meta.env.VITE_WS_URL ||
  `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.hostname}:8000`

export function useChat(conversationId: string | null) {
  const { accessToken } = useAuthStore()
  const {
    messages,
    streamingMessageId,
    appendMessage,
    updateStreamBuffer,
    updateStreamingStatus,
    finalizeMessage,
    addToolCall,
    updateToolCall,
    updateConversationTitle,
    setWs,
    setStreamingMessageId,
  } = useChatStore()
  const { addArtifact } = useResultsStore()

  // Use ref for direct WS access — avoids Zustand batching delays in sendMessage
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const conversationIdRef = useRef(conversationId)
  conversationIdRef.current = conversationId

  const pingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const connect = useCallback(() => {
    if (!conversationId || !accessToken) return
    // Don't reconnect if already open or connecting
    if (wsRef.current && wsRef.current.readyState !== WebSocket.CLOSED) return

    const socket = new WebSocket(
      `${WS_BASE}/api/v1/chat/ws/${conversationId}?token=${accessToken}`
    )
    wsRef.current = socket
    setWs(socket)

    socket.onopen = () => {
      // Send periodic pings to keep the connection alive during long LLM processing
      if (pingTimerRef.current) clearInterval(pingTimerRef.current)
      pingTimerRef.current = setInterval(() => {
        if (socket.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ type: 'ping' }))
        }
      }, 20000)
    }

    socket.onmessage = (event) => {
      let data: Record<string, unknown>
      try {
        data = JSON.parse(event.data)
      } catch {
        return
      }

      const convId = conversationIdRef.current!
      const currentStreamId = useChatStore.getState().streamingMessageId

      switch (data.type) {
        case 'text_delta': {
          if (!currentStreamId) {
            const msgId = crypto.randomUUID()
            appendMessage(convId, {
              id: msgId,
              role: 'assistant',
              content: '',
              streamBuffer: data.delta as string,
              isStreaming: true,
              startedAt: Date.now(),
            })
            setStreamingMessageId(msgId)
          } else {
            updateStreamBuffer(convId, currentStreamId, data.delta as string)
          }
          break
        }

        case 'tool_start': {
          if (!currentStreamId) {
            const msgId = crypto.randomUUID()
            appendMessage(convId, {
              id: msgId,
              role: 'assistant',
              content: '',
              tool_calls: [],
              isStreaming: true,
              startedAt: Date.now(),
            })
            setStreamingMessageId(msgId)
          }
          const streamId = useChatStore.getState().streamingMessageId!
          addToolCall(convId, streamId, {
            tool_use_id: data.tool_use_id as string,
            tool_name: data.tool as string,
            status: 'running',
            startedAt: Date.now(),
          })
          break
        }

        case 'tool_result': {
          const streamId = useChatStore.getState().streamingMessageId
          if (streamId) {
            updateToolCall(convId, streamId, data.tool_use_id as string, {
              output: data.result,
              status: 'done',
              completedAt: Date.now(),
            })
          }
          if (data.artifact) {
            const artifact = data.artifact as Record<string, unknown>
            // Multiple artifacts from a single execution (e.g. chart + excel)
            if (artifact.type === 'execution' && Array.isArray(artifact.artifacts)) {
              for (const a of artifact.artifacts as Record<string, unknown>[]) {
                addArtifact({
                  type: a.type as 'image' | 'file',
                  dataB64: a.data as string,
                  fileName: a.name as string,
                  mimeType: a.mime as string,
                  title: a.name as string,
                })
              }
            } else if (artifact.type === 'image') {
              addArtifact({
                type: 'image',
                dataB64: artifact.data as string,
                fileName: artifact.name as string,
                mimeType: artifact.mime as string,
                title: (artifact.name as string) || 'Chart',
              })
            } else if (artifact.type === 'file') {
              addArtifact({
                type: 'file',
                dataB64: artifact.data as string,
                fileName: artifact.name as string,
                mimeType: artifact.mime as string,
                title: (artifact.name as string) || 'File',
              })
            } else {
              addArtifact({
                type: artifact.type as 'table' | 'code' | 'log' | 'text',
                data: artifact.data as Record<string, unknown>[],
                content: artifact.content as string,
                language: artifact.language as string,
                title: `${data.tool} result`,
              })
            }
          }
          break
        }

        case 'message_done': {
          const streamId = useChatStore.getState().streamingMessageId
          if (streamId) {
            const msgs = useChatStore.getState().messages[convId] || []
            const msg = msgs.find((m) => m.id === streamId)
            const finalContent = (msg?.streamBuffer || msg?.content || '') as string
            finalizeMessage(convId, streamId, finalContent)
            setStreamingMessageId(null)

            // Auto-promote HTML code blocks to preview artifacts
            // Use indexOf for robustness: handles case differences, \r\n, and truncation
            const lower = finalContent.toLowerCase()
            let searchFrom = 0
            while (searchFrom < finalContent.length) {
              const fenceStart = lower.indexOf('```html', searchFrom)
              if (fenceStart === -1) break
              const newlinePos = finalContent.indexOf('\n', fenceStart)
              if (newlinePos === -1) break
              const contentStart = newlinePos + 1
              const fenceEnd = finalContent.indexOf('```', contentStart)
              const htmlContent = fenceEnd === -1
                ? finalContent.slice(contentStart).trim()
                : finalContent.slice(contentStart, fenceEnd)
              if (htmlContent) {
                addArtifact({
                  type: 'html',
                  content: htmlContent,
                  title: fenceEnd === -1 ? 'HTML Preview (truncated)' : 'HTML Preview',
                })
              }
              searchFrom = fenceEnd !== -1 ? fenceEnd + 3 : finalContent.length
            }
          }
          if (data.title && typeof data.title === 'string') {
            updateConversationTitle(convId, data.title)
          }
          break
        }

        case 'heartbeat': {
          const streamId = useChatStore.getState().streamingMessageId
          if (streamId && conversationIdRef.current) {
            const status = data.status as string
            const iter = data.iteration as number
            const elapsed = data.elapsed as number
            const label = status?.startsWith('tool:')
              ? `运行 ${status.slice(5)}...`
              : `思考中... · 第 ${iter + 1} 步 · ${elapsed}s`
            updateStreamingStatus(conversationIdRef.current, streamId, label)
          }
          break
        }

        case 'error': {
          console.error('Chat error:', data.message)
          const convId = conversationIdRef.current!
          const errId = crypto.randomUUID()
          appendMessage(convId, {
            id: errId,
            role: 'assistant',
            content: `⚠️ ${data.message || 'An error occurred'}`,
          })
          setStreamingMessageId(null)
          break
        }
      }
    }

    socket.onclose = () => {
      if (pingTimerRef.current) { clearInterval(pingTimerRef.current); pingTimerRef.current = null }
      wsRef.current = null
      setWs(null)
      // Finalize any in-progress streaming message to prevent stale state on reconnect
      const state = useChatStore.getState()
      const currentStreamId = state.streamingMessageId
      const convId = conversationIdRef.current
      if (currentStreamId && convId) {
        const msgs = state.messages[convId] || []
        const msg = msgs.find((m) => m.id === currentStreamId)
        const partialContent = (msg?.streamBuffer || msg?.content || '') as string
        state.finalizeMessage(convId, currentStreamId, partialContent)
        state.setStreamingMessageId(null)
      }
      // Auto-reconnect after 3s
      reconnectTimerRef.current = setTimeout(connect, 3000)
    }

    socket.onerror = () => {
      socket.close()
    }
  }, [conversationId, accessToken])

  useEffect(() => {
    connect()
    return () => {
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current)
      if (pingTimerRef.current) { clearInterval(pingTimerRef.current); pingTimerRef.current = null }
      if (wsRef.current) {
        // Null handlers BEFORE close so the async onclose doesn't fire
        // and overwrite wsRef with null or schedule a reconnect to the old conversation
        wsRef.current.onmessage = null
        wsRef.current.onclose = null
        wsRef.current.onerror = null
        wsRef.current.close()
        wsRef.current = null
        setWs(null)
      }
    }
  }, [connect])

  const sendMessage = useCallback(
    (content: string, skillId?: string, modelId?: string) => {
      const ws = wsRef.current
      if (!ws) {
        console.warn('WebSocket not connected')
        return
      }

      const payload = JSON.stringify({
        type: 'message',
        content,
        skill_id: skillId,
        model_id: modelId,
      })

      if (ws.readyState === WebSocket.CONNECTING) {
        // Queue until connection opens
        ws.addEventListener('open', () => ws.send(payload), { once: true })
      } else if (ws.readyState === WebSocket.OPEN) {
        ws.send(payload)
      } else {
        console.warn('WebSocket closed, cannot send')
        return
      }

      // Optimistic UI update
      if (conversationId) {
        appendMessage(conversationId, {
          id: crypto.randomUUID(),
          role: 'user',
          content,
        })
      }
    },
    [conversationId, appendMessage]
  )

  const stopStreaming = useCallback(() => {
    if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current)
    // Finalize the partial streaming message in the UI
    const state = useChatStore.getState()
    const streamId = state.streamingMessageId
    const convId = conversationIdRef.current
    if (streamId && convId) {
      const msgs = state.messages[convId] || []
      const msg = msgs.find((m) => m.id === streamId)
      const partialContent = (msg?.streamBuffer || msg?.content || '') as string
      state.finalizeMessage(convId, streamId, partialContent)
      state.setStreamingMessageId(null)
    }
    // Close the WebSocket — backend gets WebSocketDisconnect and stops streaming
    if (wsRef.current) {
      wsRef.current.onmessage = null
      wsRef.current.onclose = null
      wsRef.current.onerror = null
      wsRef.current.close()
      wsRef.current = null
      setWs(null)
    }
    // Reconnect quickly so user can send the next message
    reconnectTimerRef.current = setTimeout(connect, 300)
  }, [connect, setWs])

  return {
    messages: conversationId ? (messages[conversationId] || []) : [],
    sendMessage,
    stopStreaming,
    isStreaming: streamingMessageId !== null,
  }
}
