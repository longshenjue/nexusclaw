import { useState, useEffect } from 'react'
import { logsApi } from '../api/client'
import { FileText, Search, Plus, Clock, Trash2 } from 'lucide-react'

interface LogSource {
  id: string
  name: string
  type: 'file' | 'elasticsearch' | 'loki'
  file_pattern: string | null
  es_host: string | null
  es_index: string | null
}

interface LogEntry {
  timestamp: string
  level: string
  message: string
  source: string
  [key: string]: unknown
}

export function LogsPage() {
  const [sources, setSources] = useState<LogSource[]>([])
  const [selectedSource, setSelectedSource] = useState<string>('')
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<LogEntry[]>([])
  const [searching, setSearching] = useState(false)
  const [showAdd, setShowAdd] = useState(false)
  const [form, setForm] = useState({ name: '', description: '', type: 'file', file_pattern: '', es_host: '', es_index: '', es_username: '', es_password: '', loki_url: '', loki_username: '', loki_password: '', loki_token: '' })

  useEffect(() => {
    logsApi.listSources().then(setSources).catch(() => setSources([]))
  }, [])

  const search = async () => {
    if (!query.trim()) return
    setSearching(true)
    try {
      const data = await logsApi.search({
        query,
        source_id: selectedSource || undefined,
        limit: 100,
      })
      setResults(data.results || [])
    } catch (err) {
      console.error(err)
    } finally {
      setSearching(false)
    }
  }

  const addSource = async () => {
    try {
      await logsApi.createSource({
        name: form.name,
        description: form.description || undefined,
        type: form.type,
        file_pattern: form.type === 'file' ? form.file_pattern : undefined,
        es_host: form.type === 'elasticsearch' ? form.es_host : undefined,
        es_index_pattern: form.type === 'elasticsearch' ? form.es_index : undefined,
        es_username: form.type === 'elasticsearch' ? (form.es_username || undefined) : undefined,
        es_password: form.type === 'elasticsearch' ? (form.es_password || undefined) : undefined,
        loki_url: form.type === 'loki' ? form.loki_url : undefined,
        loki_username: form.type === 'loki' ? (form.loki_username || undefined) : undefined,
        loki_password: form.type === 'loki' ? (form.loki_password || undefined) : undefined,
        loki_token: form.type === 'loki' ? (form.loki_token || undefined) : undefined,
      })
      setSources(await logsApi.listSources())
      setShowAdd(false)
      setForm({ name: '', description: '', type: 'file', file_pattern: '', es_host: '', es_index: '', es_username: '', es_password: '', loki_url: '', loki_username: '', loki_password: '', loki_token: '' })
    } catch (err) {
      console.error(err)
    }
  }

  const deleteSource = async (id: string, name: string) => {
    if (!confirm(`Delete log source "${name}"?`)) return
    try {
      await logsApi.deleteSource(id)
      setSources((prev) => prev.filter((s) => s.id !== id))
      if (selectedSource === id) setSelectedSource('')
    } catch (err) {
      console.error(err)
    }
  }

  const levelColor = (level: string) => {
    const l = level?.toLowerCase()
    if (l === 'error' || l === 'critical') return 'text-red-400'
    if (l === 'warn' || l === 'warning') return 'text-yellow-400'
    if (l === 'info') return 'text-blue-400'
    return 'text-muted-foreground'
  }

  return (
    <div className="min-h-screen bg-background p-6">
      <div className="max-w-5xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-semibold flex items-center gap-2">
              <FileText size={22} className="text-primary" />
              Log Search
            </h1>
            <p className="text-muted-foreground text-sm mt-1">Search across file logs, Elasticsearch, and Grafana Loki</p>
          </div>
          <button
            onClick={() => setShowAdd(!showAdd)}
            className="flex items-center gap-2 bg-muted border border-border rounded-lg px-4 py-2 text-sm hover:bg-accent"
          >
            <Plus size={14} />
            Add Source
          </button>
        </div>

        {showAdd && (
          <div className="bg-card border border-border rounded-xl p-5 mb-6 space-y-3">
            <h3 className="text-sm font-medium">Add Log Source</h3>
            <div className="grid grid-cols-2 gap-3">
              <input placeholder="Name" value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                className="bg-muted border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50" />
              <select value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })}
                className="bg-muted border border-border rounded-lg px-3 py-2 text-sm">
                <option value="file">File Logs</option>
                <option value="elasticsearch">Elasticsearch</option>
                <option value="loki">Grafana Loki</option>
              </select>
            </div>
            <input
              placeholder={form.type === 'loki' ? 'Label hints for AI, e.g. server="myapp", env="prod"' : 'Description (optional)'}
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              className="w-full bg-muted border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50"
            />
            {form.type === 'file' ? (
              <input placeholder="File pattern (e.g. /var/log/app/*.log)" value={form.file_pattern}
                onChange={(e) => setForm({ ...form, file_pattern: e.target.value })}
                className="w-full bg-muted border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50" />
            ) : form.type === 'loki' ? (
              <div className="space-y-2">
                <input placeholder="Loki URL (e.g. http://loki:3100)" value={form.loki_url}
                  onChange={(e) => setForm({ ...form, loki_url: e.target.value })}
                  className="w-full bg-muted border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50" />
                <div className="grid grid-cols-2 gap-3">
                  <input placeholder="Bearer token (optional)" value={form.loki_token}
                    onChange={(e) => setForm({ ...form, loki_token: e.target.value })}
                    className="bg-muted border border-border rounded-lg px-3 py-2 text-sm focus:outline-none" />
                  <span className="flex items-center text-xs text-muted-foreground">— or use basic auth:</span>
                  <input placeholder="Username (optional)" value={form.loki_username}
                    onChange={(e) => setForm({ ...form, loki_username: e.target.value })}
                    className="bg-muted border border-border rounded-lg px-3 py-2 text-sm focus:outline-none" />
                  <input placeholder="Password (optional)" type="password" value={form.loki_password}
                    onChange={(e) => setForm({ ...form, loki_password: e.target.value })}
                    className="bg-muted border border-border rounded-lg px-3 py-2 text-sm focus:outline-none" />
                </div>
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-3">
                <input placeholder="Elasticsearch host" value={form.es_host}
                  onChange={(e) => setForm({ ...form, es_host: e.target.value })}
                  className="bg-muted border border-border rounded-lg px-3 py-2 text-sm focus:outline-none" />
                <input placeholder="Index pattern (e.g. logs-*)" value={form.es_index}
                  onChange={(e) => setForm({ ...form, es_index: e.target.value })}
                  className="bg-muted border border-border rounded-lg px-3 py-2 text-sm focus:outline-none" />
                <input placeholder="Username (optional)" value={form.es_username}
                  onChange={(e) => setForm({ ...form, es_username: e.target.value })}
                  className="bg-muted border border-border rounded-lg px-3 py-2 text-sm focus:outline-none" />
                <input placeholder="Password (optional)" type="password" value={form.es_password}
                  onChange={(e) => setForm({ ...form, es_password: e.target.value })}
                  className="bg-muted border border-border rounded-lg px-3 py-2 text-sm focus:outline-none" />
              </div>
            )}
            <div className="flex gap-2">
              <button onClick={addSource} className="bg-primary text-primary-foreground rounded-lg px-4 py-1.5 text-sm">Save</button>
              <button onClick={() => setShowAdd(false)} className="bg-muted rounded-lg px-4 py-1.5 text-sm text-muted-foreground">Cancel</button>
            </div>
          </div>
        )}

        {/* Search bar */}
        <div className="flex gap-2 mb-4">
          <div className="relative w-52 flex-shrink-0">
            <select
              value={selectedSource}
              onChange={(e) => setSelectedSource(e.target.value)}
              className="w-full bg-muted border border-border rounded-lg px-3 py-2 text-sm appearance-none pr-8"
            >
              <option value="">All sources</option>
              {sources.map((s) => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </select>
            {selectedSource && (
              <button
                onClick={() => {
                  const s = sources.find((s) => s.id === selectedSource)
                  if (s) deleteSource(s.id, s.name)
                }}
                title="Delete this source"
                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-destructive transition-colors"
              >
                <Trash2 size={13} />
              </button>
            )}
          </div>
          <div className="flex-1 flex gap-2">
            <input
              placeholder="Search logs... (e.g. 'payment failed', 'NullPointerException')"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && search()}
              className="flex-1 bg-muted border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50"
            />
            <button
              onClick={search}
              disabled={searching || !query.trim()}
              className="flex items-center gap-2 bg-primary text-primary-foreground rounded-lg px-4 py-2 text-sm disabled:opacity-50"
            >
              <Search size={14} />
              {searching ? 'Searching...' : 'Search'}
            </button>
          </div>
        </div>

        {/* Results */}
        {results.length > 0 && (
          <div className="bg-card border border-border rounded-xl overflow-hidden">
            <div className="px-4 py-2 border-b border-border text-xs text-muted-foreground">
              {results.length} results
            </div>
            <div className="divide-y divide-border/50 max-h-[60vh] overflow-y-auto">
              {results.map((entry, i) => (
                <div key={i} className="px-4 py-3 hover:bg-muted/20 font-mono text-xs">
                  <div className="flex items-center gap-3 mb-1">
                    <span className="text-muted-foreground flex items-center gap-1">
                      <Clock size={10} />
                      {entry.timestamp}
                    </span>
                    {entry.level && (
                      <span className={`font-semibold uppercase ${levelColor(entry.level)}`}>
                        {entry.level}
                      </span>
                    )}
                    {entry.source && (
                      <span className="text-muted-foreground">{entry.source}</span>
                    )}
                  </div>
                  <p className="text-foreground/90 break-all">{entry.message}</p>
                </div>
              ))}
            </div>
          </div>
        )}

        {results.length === 0 && !searching && query && (
          <div className="text-center py-16 text-muted-foreground">
            <Search size={32} className="mx-auto mb-3 opacity-30" />
            <p>No log entries found for "{query}"</p>
          </div>
        )}

        {sources.length === 0 && !showAdd && (
          <div className="text-center py-16 text-muted-foreground">
            <FileText size={32} className="mx-auto mb-3 opacity-30" />
            <p>No log sources configured. Add a file pattern or Elasticsearch source to start searching.</p>
          </div>
        )}
      </div>
    </div>
  )
}
