import { useState, useEffect } from 'react'
import { knowledgeApi } from '../api/client'
import { BookOpen, Upload, GitBranch, RefreshCw, CheckCircle, XCircle, Clock } from 'lucide-react'

interface KnowledgeSource {
  id: string
  name: string
  type: string
  status: 'pending' | 'cloning' | 'ready' | 'error'
  chunk_count: number | null
  error_msg: string | null
}

export function KnowledgePage() {
  const [sources, setSources] = useState<KnowledgeSource[]>([])
  const [uploading, setUploading] = useState(false)
  const [showRepo, setShowRepo] = useState(false)
  const [repoForm, setRepoForm] = useState({ repo_url: '', branch: 'main', access_token: '', name: '' })
  const [addingRepo, setAddingRepo] = useState(false)

  useEffect(() => {
    knowledgeApi.list().then(setSources)
  }, [])

  // Poll while any source is pending or cloning
  useEffect(() => {
    const hasInProgress = sources.some((s) => s.status === 'cloning' || s.status === 'pending')
    if (!hasInProgress) return
    const interval = setInterval(() => {
      knowledgeApi.list().then(setSources)
    }, 3000)
    return () => clearInterval(interval)
  }, [sources])

  const uploadFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true)
    try {
      await knowledgeApi.upload(file)
      setSources(await knowledgeApi.list())
    } finally {
      setUploading(false)
      e.target.value = ''
    }
  }

  const addRepo = async () => {
    if (!repoForm.repo_url.trim()) return
    setAddingRepo(true)
    try {
      await knowledgeApi.addRepo({
        repo_url: repoForm.repo_url.trim(),
        branch: repoForm.branch || 'main',
        access_token: repoForm.access_token || undefined,
        name: repoForm.name.trim() || undefined,
      })
      setSources(await knowledgeApi.list())
      setShowRepo(false)
      setRepoForm({ repo_url: '', branch: 'main', access_token: '', name: '' })
    } finally {
      setAddingRepo(false)
    }
  }

  const deleteSource = async (id: string) => {
    await knowledgeApi.delete(id)
    setSources((prev) => prev.filter((s) => s.id !== id))
  }

  return (
    <div className="min-h-screen bg-background p-6">
      <div className="max-w-4xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-semibold flex items-center gap-2">
              <BookOpen size={22} className="text-primary" />
              Knowledge Base
            </h1>
            <p className="text-muted-foreground text-sm mt-1">
              Upload documents or connect Git repositories for AI-assisted code exploration
            </p>
          </div>
          <div className="flex gap-2">
            <label className={`flex items-center gap-2 bg-primary text-primary-foreground rounded-lg px-4 py-2 text-sm cursor-pointer hover:bg-primary/90 ${uploading ? 'opacity-50 cursor-not-allowed' : ''}`}>
              <Upload size={14} />
              {uploading ? 'Uploading...' : 'Upload File'}
              <input type="file" className="hidden" accept=".pdf,.md,.txt,.markdown" onChange={uploadFile} disabled={uploading} />
            </label>
            <button
              onClick={() => setShowRepo(!showRepo)}
              className="flex items-center gap-2 bg-muted border border-border rounded-lg px-4 py-2 text-sm hover:bg-accent"
            >
              <GitBranch size={14} />
              Add Git Repo
            </button>
          </div>
        </div>

        {showRepo && (
          <div className="bg-card border border-border rounded-xl p-5 mb-6 space-y-3">
            <h3 className="text-sm font-medium flex items-center gap-2">
              <GitBranch size={14} /> Connect Git Repository
            </h3>
            <p className="text-xs text-muted-foreground">
              Supports GitHub, GitLab, and any public or private git repository URL.
              The repository will be cloned locally for fast code search.
            </p>
            <input
              placeholder="https://github.com/owner/repo or https://gitlab.com/owner/repo"
              value={repoForm.repo_url}
              onChange={(e) => setRepoForm({ ...repoForm, repo_url: e.target.value })}
              className="w-full bg-muted border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50"
            />
            <div className="grid grid-cols-2 gap-3">
              <input
                placeholder="Branch (default: main)"
                value={repoForm.branch}
                onChange={(e) => setRepoForm({ ...repoForm, branch: e.target.value })}
                className="bg-muted border border-border rounded-lg px-3 py-2 text-sm focus:outline-none"
              />
              <input
                placeholder="Display name (optional)"
                value={repoForm.name}
                onChange={(e) => setRepoForm({ ...repoForm, name: e.target.value })}
                className="bg-muted border border-border rounded-lg px-3 py-2 text-sm focus:outline-none"
              />
            </div>
            <input
              placeholder="Access token (for private repos)"
              type="password"
              value={repoForm.access_token}
              onChange={(e) => setRepoForm({ ...repoForm, access_token: e.target.value })}
              className="w-full bg-muted border border-border rounded-lg px-3 py-2 text-sm focus:outline-none"
            />
            <div className="flex gap-2">
              <button
                onClick={addRepo}
                disabled={addingRepo || !repoForm.repo_url.trim()}
                className="bg-primary text-primary-foreground rounded-lg px-4 py-1.5 text-sm disabled:opacity-50"
              >
                {addingRepo ? 'Adding...' : 'Clone Repository'}
              </button>
              <button onClick={() => setShowRepo(false)} className="bg-muted rounded-lg px-4 py-1.5 text-sm text-muted-foreground">
                Cancel
              </button>
            </div>
          </div>
        )}

        {/* Sources list */}
        <div className="space-y-2">
          {sources.map((source) => (
            <div key={source.id} className="bg-card border border-border rounded-xl p-4 flex items-center gap-4">
              <StatusIcon status={source.status} />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium truncate">{source.name}</span>
                  <span className="text-xs text-muted-foreground bg-muted px-1.5 py-0.5 rounded">
                    {source.type === 'github_repo' ? 'git repo' : source.type}
                  </span>
                </div>
                {source.status === 'error' && (
                  <p className="text-xs text-destructive mt-0.5">{source.error_msg}</p>
                )}
                {(source.status === 'cloning' || source.status === 'pending') && (
                  <p className="text-xs text-muted-foreground mt-0.5 flex items-center gap-1">
                    <RefreshCw size={10} className="animate-spin" />
                    {source.status === 'pending' ? 'Queued...' : 'Cloning repository...'}
                  </p>
                )}
                {source.status === 'ready' && source.type === 'github_repo' && (
                  <p className="text-xs text-muted-foreground mt-0.5">Ready — use grep_code, read_code_file, git_log in chat</p>
                )}
              </div>
              <button onClick={() => deleteSource(source.id)} className="text-xs text-muted-foreground hover:text-destructive transition-colors">
                Remove
              </button>
            </div>
          ))}
          {sources.length === 0 && (
            <div className="text-center py-16 text-muted-foreground">
              <BookOpen size={32} className="mx-auto mb-3 opacity-30" />
              <p>No knowledge sources yet. Upload a document or connect a Git repository.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function StatusIcon({ status }: { status: string }) {
  switch (status) {
    case 'ready': return <CheckCircle size={16} className="text-green-400 flex-shrink-0" />
    case 'error': return <XCircle size={16} className="text-destructive flex-shrink-0" />
    case 'cloning': return <RefreshCw size={16} className="text-primary animate-spin flex-shrink-0" />
    default: return <Clock size={16} className="text-muted-foreground flex-shrink-0" />
  }
}
