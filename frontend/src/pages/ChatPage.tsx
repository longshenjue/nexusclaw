import { useEffect, useState } from 'react'
import { useNavigate, useParams, Link } from 'react-router-dom'
import { conversationsApi } from '../api/client'
import { useChatStore, type Conversation } from '../stores/chatStore'
import { useAuthStore } from '../stores/authStore'
import { ChatInterface } from '../components/chat/ChatInterface'
import { cn } from '../lib/utils'
import { MessageSquare, Plus, Settings, BookOpen, Database, LogOut, ChevronLeft, ChevronRight, Shield, FileText } from 'lucide-react'

export function ChatPage() {
  const { conversationId } = useParams<{ conversationId: string }>()
  const navigate = useNavigate()
  const { user, logout } = useAuthStore()
  const { conversations, setConversations, addConversation, removeConversation, activeConversationId, setActiveConversation } = useChatStore()
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)

  useEffect(() => {
    conversationsApi.list().then(setConversations).catch(console.error)
  }, [])

  useEffect(() => {
    if (conversationId) setActiveConversation(conversationId)
  }, [conversationId])

  const createNewConversation = async () => {
    try {
      const conv = await conversationsApi.create({ title: 'New Chat' })
      addConversation(conv)
      navigate(`/chat/${conv.id}`)
    } catch (err) {
      console.error(err)
    }
  }

  const deleteConversation = async (id: string, e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    try {
      await conversationsApi.delete(id)
      removeConversation(id)
      if (activeConversationId === id) navigate('/chat')
    } catch (err) {
      console.error(err)
    }
  }

  return (
    <div className="flex h-screen bg-background overflow-hidden">
      {/* Left sidebar: Conversations */}
      <div className={cn(
        'flex flex-col bg-sidebar border-r border-border transition-all duration-200',
        sidebarCollapsed ? 'w-12' : 'w-60'
      )}>
        {/* Header */}
        <div className="flex items-center gap-2 p-3 border-b border-border">
          {!sidebarCollapsed && (
            <div className="flex items-center gap-2 flex-1 min-w-0">
              <div className="w-6 h-6 rounded bg-primary flex items-center justify-center flex-shrink-0">
                <span className="text-white text-xs font-bold">N</span>
              </div>
              <span className="text-sm font-medium text-foreground truncate">NexusClaw</span>
            </div>
          )}
          <button
            onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
            className="p-1 rounded hover:bg-accent text-muted-foreground"
          >
            {sidebarCollapsed ? <ChevronRight size={14} /> : <ChevronLeft size={14} />}
          </button>
        </div>

        {/* New chat button */}
        <div className="p-2">
          <button
            onClick={createNewConversation}
            className={cn(
              'flex items-center gap-2 rounded-lg px-2 py-2 text-sm text-muted-foreground hover:text-foreground hover:bg-accent w-full transition-colors',
              sidebarCollapsed ? 'justify-center' : ''
            )}
          >
            <Plus size={14} />
            {!sidebarCollapsed && <span>New Chat</span>}
          </button>
        </div>

        {/* Conversations list */}
        <div className="flex-1 overflow-y-auto px-2 space-y-0.5">
          {conversations.map((conv: Conversation) => (
            <Link
              key={conv.id}
              to={`/chat/${conv.id}`}
              className={cn(
                'flex items-center gap-2 rounded-lg px-2 py-2 text-sm transition-colors group',
                activeConversationId === conv.id
                  ? 'bg-accent text-foreground'
                  : 'text-muted-foreground hover:bg-accent/50 hover:text-foreground'
              )}
            >
              <MessageSquare size={13} className="flex-shrink-0" />
              {!sidebarCollapsed && (
                <>
                  <span className="truncate flex-1 text-xs">{conv.title}</span>
                  <button
                    onClick={(e) => deleteConversation(conv.id, e)}
                    className="opacity-0 group-hover:opacity-100 text-muted-foreground hover:text-destructive transition-opacity text-xs"
                  >
                    ×
                  </button>
                </>
              )}
            </Link>
          ))}
          {conversations.length === 0 && !sidebarCollapsed && (
            <p className="text-xs text-muted-foreground text-center py-4">No conversations yet</p>
          )}
        </div>

        {/* Bottom nav */}
        <div className="border-t border-border p-2 space-y-0.5">
          {[
            { icon: BookOpen, label: 'Knowledge', to: '/knowledge' },
            { icon: FileText, label: 'Logs', to: '/logs' },
            { icon: Database, label: 'Datasources', to: '/admin/datasources' },
            ...(user?.role === 'admin' ? [{ icon: Shield, label: 'Admin', to: '/admin/users' }] : []),
          ].map(({ icon: Icon, label, to }) => (
            <Link
              key={to}
              to={to}
              className={cn(
                'flex items-center gap-2 rounded-lg px-2 py-1.5 text-xs text-muted-foreground hover:text-foreground hover:bg-accent transition-colors',
                sidebarCollapsed ? 'justify-center' : ''
              )}
            >
              <Icon size={13} />
              {!sidebarCollapsed && <span>{label}</span>}
            </Link>
          ))}

          {/* User */}
          <div className={cn('flex items-center gap-2 mt-2 px-2 py-1', sidebarCollapsed ? 'justify-center' : '')}>
            <div className="w-6 h-6 rounded-full bg-primary/20 flex items-center justify-center text-primary text-xs font-medium flex-shrink-0">
              {user?.username?.[0]?.toUpperCase()}
            </div>
            {!sidebarCollapsed && (
              <>
                <span className="text-xs text-muted-foreground flex-1 truncate">{user?.username}</span>
                <button
                  onClick={() => { logout(); navigate('/login') }}
                  className="text-muted-foreground hover:text-destructive"
                >
                  <LogOut size={12} />
                </button>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 min-w-0">
        {conversationId ? (
          <ChatInterface conversationId={conversationId} />
        ) : (
          <EmptyState onCreate={createNewConversation} />
        )}
      </div>
    </div>
  )
}

function EmptyState({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="h-full flex items-center justify-center">
      <div className="text-center max-w-md">
        <div className="w-16 h-16 rounded-2xl bg-primary/10 flex items-center justify-center mx-auto mb-4">
          <MessageSquare size={28} className="text-primary" />
        </div>
        <h2 className="text-xl font-semibold text-foreground mb-2">IT Operations Assistant</h2>
        <p className="text-muted-foreground text-sm mb-6">
          Submit tickets, diagnose bugs, query databases, analyze logs — all with AI assistance.
        </p>
        <div className="grid grid-cols-2 gap-3 text-left mb-6">
          {[
            { icon: '🔍', text: 'Diagnose payment failures' },
            { icon: '🗄️', text: 'Query database records' },
            { icon: '📋', text: 'Analyze error logs' },
            { icon: '⚡', text: 'Run troubleshooting skills' },
          ].map(({ icon, text }) => (
            <div key={text} className="bg-card border border-border rounded-lg p-3 text-sm">
              <span className="mr-2">{icon}</span>
              <span className="text-muted-foreground">{text}</span>
            </div>
          ))}
        </div>
        <button
          onClick={onCreate}
          className="bg-primary text-primary-foreground rounded-lg px-6 py-2 text-sm font-medium hover:bg-primary/90 transition-colors"
        >
          Start New Chat
        </button>
      </div>
    </div>
  )
}
