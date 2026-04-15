import { useEffect, useRef, useState, useCallback } from 'react'
import { conversationsApi } from '../../api/client'
import { useChatStore, type Message } from '../../stores/chatStore'
import { useResultsStore, type Artifact } from '../../stores/resultsStore'
import { useChat } from '../../hooks/useChat'
import { cn } from '../../lib/utils'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Send, Square, ChevronDown, Table2, Code2, FileText, Terminal, CheckCircle2, Copy, Check, Monitor } from 'lucide-react'

// Format milliseconds into "Xs" or "Xm Ys"
function formatDuration(ms: number): string {
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  const rem = s % 60
  return rem > 0 ? `${m}m ${rem}s` : `${m}m`
}

// Live elapsed timer — ticks every second while active
function ElapsedTimer({ startedAt }: { startedAt: number }) {
  const [elapsed, setElapsed] = useState(() => Date.now() - startedAt)
  useEffect(() => {
    const id = setInterval(() => setElapsed(Date.now() - startedAt), 1000)
    return () => clearInterval(id)
  }, [startedAt])
  return <span>{formatDuration(elapsed)}</span>
}

interface ChatInterfaceProps {
  conversationId: string
}

/**
 * Normalize markdown before parsing.
 * LLMs sometimes output GFM tables as single collapsed lines using `||` as a
 * row separator: `| h1 | h2 ||---|---|| r1 | r2 |`
 * This converts each `||` at a table row boundary into a real newline so
 * remark-gfm can parse it as a proper table.
 */
function preprocessMarkdown(content: string): string {
  // Strip Bedrock XML tool artifacts
  let result = content
    .replace(/<tool_call>[\s\S]*?<\/tool_call>/g, '')
    .replace(/<tool_response>[\s\S]*?<\/tool_response>/g, '')
    .trim()

  // Expand collapsed GFM table rows: `| h1 | h2 ||---|---|| r1 | r2 |`
  const lines = result.split('\n')
  const expanded: string[] = []
  for (const line of lines) {
    const trimmed = line.trim()
    if (trimmed.startsWith('|') && trimmed.includes('||')) {
      const rows = trimmed.split('||').map((r) => {
        let row = r.trim()
        if (!row) return null
        if (!row.startsWith('|')) row = '|' + row
        if (!row.endsWith('|')) row = row + '|'
        return row
      }).filter((r): r is string => r !== null)
      expanded.push(...rows)
    } else {
      expanded.push(line)
    }
  }

  // Ensure a blank line before any table row (line starting with |)
  // so remark-gfm treats it as a block table, not inline paragraph text
  const withBlankLines: string[] = []
  for (let i = 0; i < expanded.length; i++) {
    const line = expanded[i]
    const prevLine = i > 0 ? expanded[i - 1] : ''
    const isTableLine = line.trim().startsWith('|')
    const prevIsEmpty = prevLine.trim() === ''
    const prevIsTable = prevLine.trim().startsWith('|')
    if (isTableLine && !prevIsEmpty && !prevIsTable && i > 0) {
      withBlankLines.push('')
    }
    withBlankLines.push(line)
  }

  return withBlankLines.join('\n')
}

export function ChatInterface({ conversationId }: ChatInterfaceProps) {
  const { messages, sendMessage, stopStreaming, isStreaming } = useChat(conversationId)
  const setMessages = useChatStore((s) => s.setMessages)
  const clearArtifacts = useResultsStore((s) => s.clearArtifacts)
  const [input, setInput] = useState('')
  const [showResults, setShowResults] = useState(true)
  const [resultsWidth, setResultsWidth] = useState(420)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const isDragging = useRef(false)
  const dragStartX = useRef(0)
  const dragStartWidth = useRef(0)

  const handleDragStart = useCallback((e: React.MouseEvent) => {
    isDragging.current = true
    dragStartX.current = e.clientX
    dragStartWidth.current = resultsWidth
    e.preventDefault()
  }, [resultsWidth])

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isDragging.current) return
      const delta = dragStartX.current - e.clientX
      setResultsWidth(Math.max(280, Math.min(900, dragStartWidth.current + delta)))
    }
    const handleMouseUp = () => { isDragging.current = false }
    document.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseup', handleMouseUp)
    return () => {
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
    }
  }, [])

  // Load conversation messages
  useEffect(() => {
    conversationsApi.getMessages(conversationId).then((msgs) => {
      setMessages(conversationId, msgs)
    }).catch(console.error)
    clearArtifacts()
  }, [conversationId])

  // Auto-scroll
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const handleSend = () => {
    if (!input.trim() || isStreaming) return
    sendMessage(input.trim())
    setInput('')
    if (textareaRef.current) textareaRef.current.style.height = 'auto'
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleTextareaChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value)
    e.target.style.height = 'auto'
    e.target.style.height = Math.min(e.target.scrollHeight, 200) + 'px'
  }

  return (
    <div className="flex h-full">
      {/* Chat panel */}
      <div className={cn('flex flex-col', showResults ? 'flex-1 min-w-0' : 'w-full')}>
        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {messages.length === 0 && (
            <div className="flex items-center justify-center h-full">
              <p className="text-muted-foreground text-sm">Send a message to start the conversation</p>
            </div>
          )}
          {messages.map((msg) => (
            <MessageBubble key={msg.id} message={msg} />
          ))}
          <div ref={messagesEndRef} />
        </div>

        {/* Input area */}
        <div className="border-t border-border p-3">
          <div className="flex gap-2 items-end">
            <div className="flex-1 bg-muted border border-border rounded-xl focus-within:ring-2 focus-within:ring-primary/30">
              <textarea
                ref={textareaRef}
                value={input}
                onChange={handleTextareaChange}
                onKeyDown={handleKeyDown}
                placeholder="Describe the issue or ask a question... (Enter to send, Shift+Enter for newline)"
                className="w-full bg-transparent px-4 py-3 text-sm text-foreground resize-none focus:outline-none min-h-[44px] max-h-[200px]"
                rows={1}
                disabled={isStreaming}
              />
            </div>
            {isStreaming ? (
              <button
                onClick={stopStreaming}
                className="bg-destructive text-destructive-foreground rounded-xl p-3 hover:bg-destructive/90 transition-colors flex-shrink-0"
                title="Stop generating"
              >
                <Square size={16} />
              </button>
            ) : (
              <button
                onClick={handleSend}
                disabled={!input.trim()}
                className="bg-primary text-primary-foreground rounded-xl p-3 hover:bg-primary/90 transition-colors disabled:opacity-40 flex-shrink-0"
              >
                <Send size={16} />
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Drag handle + toggle — sits between chat and results */}
      <div
        onMouseDown={showResults ? handleDragStart : undefined}
        onClick={!showResults ? () => setShowResults(true) : undefined}
        className={cn(
          'flex-shrink-0 w-1.5 border-l border-border flex flex-col items-center justify-center gap-1 transition-colors',
          showResults
            ? 'cursor-col-resize hover:bg-primary/20 bg-muted/40'
            : 'cursor-pointer hover:bg-accent bg-muted w-6'
        )}
        title={showResults ? 'Drag to resize · Click chevron to hide' : 'Show results panel'}
      >
        {showResults ? (
          <>
            <div className="w-0.5 h-6 rounded-full bg-border/80" />
            <button
              onMouseDown={(e) => e.stopPropagation()}
              onClick={() => setShowResults(false)}
              className="absolute mt-0 p-0.5 rounded hover:bg-accent"
              title="Hide results panel"
            >
              <ChevronDown size={10} className="text-muted-foreground rotate-90" />
            </button>
          </>
        ) : (
          <ChevronDown size={12} className="text-muted-foreground -rotate-90" />
        )}
      </div>

      {/* Results panel */}
      {showResults && <ResultsPanel width={resultsWidth} />}
    </div>
  )
}

function CodeBlock({ className, children }: { className?: string; children?: React.ReactNode }) {
  const [copied, setCopied] = useState(false)
  const { addArtifact } = useResultsStore()
  const lang = className?.replace('language-', '') || ''
  const code = String(children).replace(/\n$/, '')

  const copy = () => {
    navigator.clipboard.writeText(code)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const preview = () => {
    addArtifact({ type: 'html', content: code, title: 'HTML Preview' })
  }

  return (
    <div className="relative group my-3 rounded-lg overflow-hidden border border-white/10">
      <div className="flex items-center justify-between px-4 py-1.5 bg-white/5 border-b border-white/10">
        <span className="text-[11px] text-white/40 font-mono uppercase tracking-wider">{lang || 'code'}</span>
        <div className="flex items-center gap-2">
          {lang === 'html' && (
            <button
              onClick={preview}
              className="flex items-center gap-1 text-[11px] text-white/40 hover:text-white/70 transition-colors"
            >
              <Monitor size={11} />
              Preview
            </button>
          )}
          <button
            onClick={copy}
            className="flex items-center gap-1 text-[11px] text-white/40 hover:text-white/70 transition-colors"
          >
            {copied ? <Check size={11} className="text-green-400" /> : <Copy size={11} />}
            {copied ? 'Copied' : 'Copy'}
          </button>
        </div>
      </div>
      <pre className="bg-[#0d1117] px-4 py-3 overflow-x-auto text-[12.5px] leading-relaxed">
        <code className="text-[#e6edf3] font-mono">{code}</code>
      </pre>
    </div>
  )
}

// Parse a single table row's cells
function parseTableRow(line: string): string[] {
  return line.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim())
}

// True if the line is a GFM separator row: |---|---|
function isTableSeparator(line: string): boolean {
  return /^\|[\s\-:|]+\|$/.test(line.trim())
}

type MarkdownBlock =
  | { type: 'text'; content: string }
  | { type: 'table'; headers: string[]; rows: string[][] }

// Split markdown into text blocks and table blocks
function splitBlocks(content: string): MarkdownBlock[] {
  const lines = content.split('\n')
  const result: MarkdownBlock[] = []
  const textAcc: string[] = []
  let i = 0

  while (i < lines.length) {
    const line = lines[i]
    // Start of a table: current line starts with | and next line is a separator
    if (
      line.trim().startsWith('|') &&
      i + 1 < lines.length &&
      isTableSeparator(lines[i + 1])
    ) {
      if (textAcc.length > 0) {
        result.push({ type: 'text', content: textAcc.join('\n') })
        textAcc.length = 0
      }
      const tableLines: string[] = []
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        tableLines.push(lines[i])
        i++
      }
      const headers = parseTableRow(tableLines[0])
      const rows = tableLines.slice(2).map(parseTableRow)
      result.push({ type: 'table', headers, rows })
    } else {
      textAcc.push(line)
      i++
    }
  }

  if (textAcc.length > 0) {
    result.push({ type: 'text', content: textAcc.join('\n') })
  }

  return result
}

// Render inline markdown (bold, code, etc.) inside table cells using ReactMarkdown
function InlineCell({ content }: { content: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        p: ({ children }) => <>{children}</>,
        strong: ({ children }) => <strong className="font-semibold text-foreground">{children}</strong>,
        em: ({ children }) => <em className="italic text-foreground/80">{children}</em>,
        code: ({ children }) => (
          <code className="bg-white/10 text-primary/90 rounded px-1 py-0 text-[11px] font-mono border border-white/10">
            {children}
          </code>
        ),
      }}
    >
      {content}
    </ReactMarkdown>
  )
}

const MARKDOWN_COMPONENTS = {
  h1: ({ children }: { children?: React.ReactNode }) => (
    <h1 className="text-lg font-bold text-foreground mt-5 mb-3 pb-1.5 border-b border-white/10 first:mt-0">{children}</h1>
  ),
  h2: ({ children }: { children?: React.ReactNode }) => (
    <h2 className="text-base font-semibold text-foreground mt-4 mb-2 pb-1 border-b border-white/5 first:mt-0">{children}</h2>
  ),
  h3: ({ children }: { children?: React.ReactNode }) => (
    <h3 className="text-sm font-semibold text-foreground/90 mt-3 mb-1.5 first:mt-0">{children}</h3>
  ),
  h4: ({ children }: { children?: React.ReactNode }) => (
    <h4 className="text-sm font-medium text-foreground/80 mt-2 mb-1 first:mt-0">{children}</h4>
  ),
  p: ({ children }: { children?: React.ReactNode }) => (
    <p className="text-foreground/90 leading-relaxed mb-2 last:mb-0">{children}</p>
  ),
  ul: ({ children }: { children?: React.ReactNode }) => (
    <ul className="my-2 space-y-1">{children}</ul>
  ),
  ol: ({ children }: { children?: React.ReactNode }) => (
    <ol className="my-2 space-y-1 pl-4 list-decimal">{children}</ol>
  ),
  li: ({ children }: { children?: React.ReactNode }) => (
    <li className="flex gap-2 text-foreground/90 text-sm leading-relaxed">
      <span className="text-primary/60 flex-shrink-0 mt-[3px] select-none text-[10px]">◆</span>
      <div className="flex-1 min-w-0">{children}</div>
    </li>
  ),
  code: ({ className, children, ...props }: { className?: string; children?: React.ReactNode }) => {
    const isBlock = !!className
    if (isBlock) return <CodeBlock className={className}>{children}</CodeBlock>
    return (
      <code className="bg-white/10 text-primary/90 rounded px-1.5 py-0.5 text-[12px] font-mono border border-white/10" {...props}>
        {children}
      </code>
    )
  },
  pre: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  blockquote: ({ children }: { children?: React.ReactNode }) => (
    <blockquote className="border-l-2 border-primary/50 pl-3 my-2 text-foreground/70 italic">{children}</blockquote>
  ),
  hr: () => <hr className="my-4 border-white/10" />,
  strong: ({ children }: { children?: React.ReactNode }) => (
    <strong className="font-semibold text-foreground">{children}</strong>
  ),
  em: ({ children }: { children?: React.ReactNode }) => (
    <em className="italic text-foreground/80">{children}</em>
  ),
  a: ({ href, children }: { href?: string; children?: React.ReactNode }) => (
    <a href={href} target="_blank" rel="noopener noreferrer"
      className="text-primary underline underline-offset-2 hover:text-primary/80 transition-colors">
      {children}
    </a>
  ),
}

function MarkdownContent({ content, isStreaming }: { content: string; isStreaming?: boolean }) {
  const normalized = preprocessMarkdown(content)
  const blocks = splitBlocks(normalized)

  return (
    <div className={cn('text-sm leading-relaxed', isStreaming && 'streaming-cursor')}>
      {blocks.map((block, idx) => {
        if (block.type === 'table') {
          return (
            <div key={idx} className="my-3 overflow-x-auto rounded-lg border border-white/10">
              <table className="w-full text-xs border-collapse">
                <thead className="bg-white/5 border-b border-white/10">
                  <tr>
                    {block.headers.map((h, j) => (
                      <th key={j} className="text-left px-3 py-2 text-[11px] font-medium text-white/50 uppercase tracking-wide whitespace-nowrap">
                        <InlineCell content={h} />
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {block.rows.map((row, j) => (
                    <tr key={j} className="border-b border-white/5 last:border-0 hover:bg-white/[0.03] transition-colors">
                      {row.map((cell, k) => (
                        <td key={k} className="px-3 py-2 text-foreground/85">
                          <InlineCell content={cell} />
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
        return (
          <ReactMarkdown key={idx} remarkPlugins={[remarkGfm]} components={MARKDOWN_COMPONENTS}>
            {block.content}
          </ReactMarkdown>
        )
      })}
    </div>
  )
}

function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === 'user'
  const content = message.streamBuffer !== undefined ? message.streamBuffer : message.content

  return (
    <div className={cn('flex gap-3', isUser ? 'justify-end' : 'justify-start')}>
      {!isUser && (
        <div className="w-7 h-7 rounded-full bg-primary/20 flex items-center justify-center flex-shrink-0 mt-0.5">
          <span className="text-primary text-xs font-bold">AI</span>
        </div>
      )}
      <div className={cn('max-w-[75%]', isUser ? 'items-end' : 'items-start', 'flex flex-col gap-1')}>
        {/* Tool calls */}
        {message.tool_calls && message.tool_calls.length > 0 && (
          <div className="space-y-1 w-full">
            {message.tool_calls.map((tc) => (
              <div key={tc.tool_use_id} className="flex items-center gap-2 bg-muted/60 border border-border rounded-lg px-3 py-1.5 text-xs">
                <div className={cn(
                  'w-1.5 h-1.5 rounded-full flex-shrink-0',
                  tc.status === 'running' ? 'bg-yellow-400 animate-pulse' : 'bg-green-400'
                )} />
                <span className="text-muted-foreground">Tool:</span>
                <span className="text-foreground font-mono">{tc.tool_name}</span>
                <span className="ml-auto text-muted-foreground font-mono">
                  {tc.status === 'running' && tc.startedAt ? (
                    <ElapsedTimer startedAt={tc.startedAt} />
                  ) : tc.startedAt && tc.completedAt ? (
                    formatDuration(tc.completedAt - tc.startedAt)
                  ) : null}
                </span>
              </div>
            ))}
          </div>
        )}

        {/* Message content */}
        {content && (
          <div className={cn(
            'rounded-2xl px-4 py-2.5 text-sm',
            isUser
              ? 'bg-primary text-primary-foreground rounded-br-sm'
              : 'bg-card border border-border text-foreground rounded-bl-sm'
          )}>
            {isUser ? (
              <p className="whitespace-pre-wrap">{content}</p>
            ) : (
              <MarkdownContent content={content} isStreaming={message.isStreaming} />
            )}
          </div>
        )}

        {/* Elapsed time / token count row */}
        {!isUser && (
          <div className="flex items-center gap-2 px-1">
            {message.isStreaming && message.startedAt ? (
              <span className="text-xs text-muted-foreground flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse inline-block" />
                <ElapsedTimer startedAt={message.startedAt} />
                {message.streamingStatus && (
                  <span className="text-muted-foreground/70">· {message.streamingStatus}</span>
                )}
              </span>
            ) : message.duration !== undefined ? (
              <span className="text-xs text-muted-foreground flex items-center gap-1">
                <CheckCircle2 size={11} className="text-green-500" />
                {formatDuration(message.duration)}
              </span>
            ) : null}
            {message.token_count && !message.isStreaming && (
              <span className="text-xs text-muted-foreground">{message.token_count} tokens</span>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function ResultsPanel({ width }: { width: number }) {
  const { artifacts, activeArtifactId, setActiveArtifact } = useResultsStore()

  return (
    <div className="flex flex-col border-l border-border bg-card flex-shrink-0" style={{ width }}>
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border">
        <span className="text-xs font-medium text-foreground">Results</span>
        <span className="text-xs text-muted-foreground">({artifacts.length})</span>
      </div>

      {/* Tabs */}
      {artifacts.length > 0 && (
        <div className="flex overflow-x-auto border-b border-border px-2 gap-1 py-1">
          {artifacts.map((a, i) => (
            <button
              key={a.id}
              onClick={() => setActiveArtifact(a.id)}
              className={cn(
                'flex items-center gap-1 px-2 py-1 rounded text-xs whitespace-nowrap transition-colors',
                activeArtifactId === a.id
                  ? 'bg-accent text-foreground'
                  : 'text-muted-foreground hover:text-foreground'
              )}
            >
              <ArtifactIcon type={a.type} />
              <span>{a.title || `#${artifacts.length - i}`}</span>
            </button>
          ))}
        </div>
      )}

      {/* Active artifact display */}
      <div className="flex-1 overflow-auto">
        {artifacts.length === 0 ? (
          <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
            <div className="text-center px-4">
              <Code2 size={24} className="mx-auto mb-2 opacity-30" />
              <p className="mb-1">Results will appear here</p>
              <p className="text-xs opacity-60">Tables, code, and logs from tool calls show up here</p>
            </div>
          </div>
        ) : (
          (() => {
            const active = artifacts.find((a) => a.id === activeArtifactId) || artifacts[0]
            return active ? <ArtifactDisplay artifact={active} /> : null
          })()
        )}
      </div>
    </div>
  )
}

function ArtifactIcon({ type }: { type: Artifact['type'] }) {
  switch (type) {
    case 'table': return <Table2 size={11} />
    case 'code': return <Code2 size={11} />
    case 'log': return <Terminal size={11} />
    case 'html': return <Monitor size={11} />
    default: return <FileText size={11} />
  }
}

function ArtifactDisplay({ artifact }: { artifact: Artifact }) {
  if (artifact.type === 'html' && artifact.content) {
    return (
      <div className="h-full flex flex-col">
        <iframe
          sandbox="allow-scripts"
          srcDoc={artifact.content}
          className="flex-1 w-full border-0 bg-white"
          title="HTML Preview"
        />
      </div>
    )
  }

  if (artifact.type === 'image' && artifact.dataB64) {
    return (
      <div className="p-3 flex flex-col gap-2">
        {artifact.fileName && (
          <div className="text-xs text-muted-foreground">{artifact.fileName}</div>
        )}
        <img
          src={`data:${artifact.mimeType || 'image/png'};base64,${artifact.dataB64}`}
          alt={artifact.fileName || 'Chart'}
          className="max-w-full rounded-lg border border-border"
        />
      </div>
    )
  }

  if (artifact.type === 'file' && artifact.dataB64) {
    const handleDownload = () => {
      const byteChars = atob(artifact.dataB64!)
      const byteNums = new Uint8Array(byteChars.length)
      for (let i = 0; i < byteChars.length; i++) byteNums[i] = byteChars.charCodeAt(i)
      const blob = new Blob([byteNums], { type: artifact.mimeType || 'application/octet-stream' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = artifact.fileName || 'download'
      a.click()
      URL.revokeObjectURL(url)
    }
    return (
      <div className="p-4 flex flex-col gap-3">
        <div className="flex items-center gap-3 p-3 rounded-lg border border-border bg-muted/30">
          <div className="text-2xl">📄</div>
          <div className="flex-1 min-w-0">
            <div className="text-sm font-medium truncate">{artifact.fileName || 'File'}</div>
            <div className="text-xs text-muted-foreground">{artifact.mimeType || 'file'}</div>
          </div>
          <button
            onClick={handleDownload}
            className="shrink-0 px-3 py-1.5 text-xs font-medium rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
          >
            Download
          </button>
        </div>
      </div>
    )
  }

  if (artifact.type === 'table' && artifact.data) {
    const columns = artifact.data.length > 0 ? Object.keys(artifact.data[0]) : []
    return (
      <div className="p-3">
        <div className="text-xs text-muted-foreground mb-2">{artifact.data.length} rows</div>
        <div className="overflow-auto rounded-lg border border-border">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-border bg-muted/50">
                {columns.map((col) => (
                  <th key={col} className="text-left px-3 py-2 text-muted-foreground font-medium whitespace-nowrap">
                    {col}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {artifact.data.map((row, i) => (
                <tr key={i} className="border-b border-border/50 hover:bg-muted/30">
                  {columns.map((col) => (
                    <td key={col} className="px-3 py-2 text-foreground whitespace-nowrap max-w-[200px] truncate">
                      {String(row[col] ?? '')}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    )
  }

  if (artifact.type === 'code' && artifact.content) {
    return (
      <div className="p-3">
        <div className="text-xs text-muted-foreground mb-2">{artifact.language || 'code'}</div>
        <pre className="bg-muted rounded-lg p-3 text-xs text-foreground overflow-auto font-mono whitespace-pre-wrap">
          {artifact.content}
        </pre>
      </div>
    )
  }

  if (artifact.type === 'log' && artifact.content) {
    return (
      <div className="p-3 h-full">
        <pre className="bg-muted rounded-lg p-3 text-xs font-mono overflow-auto h-full whitespace-pre-wrap text-green-400">
          {artifact.content}
        </pre>
      </div>
    )
  }

  return (
    <div className="p-3">
      <p className="text-sm text-foreground whitespace-pre-wrap">{artifact.content || JSON.stringify(artifact.data, null, 2)}</p>
    </div>
  )
}
