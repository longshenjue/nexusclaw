import { Link } from 'react-router-dom'
import { useState, useEffect } from 'react'
import { adminApi, datasourcesApi, mcpApi, modelsApi, skillsApi, knowledgeApi } from '../api/client'
import { useAuthStore } from '../stores/authStore'
import { cn } from '../lib/utils'
import { Users, Database, Cpu, Zap, FileText, Shield, X, ChevronLeft } from 'lucide-react'

type AdminTab = 'users' | 'models' | 'datasources' | 'mcp' | 'skills' | 'audit'

export function AdminPage() {
  const { user } = useAuthStore()
  const [tab, setTab] = useState<AdminTab>('users')

  if (user?.role !== 'admin') {
    return <div className="p-8 text-muted-foreground">Access denied</div>
  }

  return (
    <div className="flex h-screen bg-background">
      {/* Admin sidebar */}
      <div className="w-48 bg-sidebar border-r border-border flex flex-col">
        <div className="p-3 border-b border-border">
            <Link to="/chat" className="flex items-center gap-1.5 text-muted-foreground hover:text-foreground text-xs mb-3 transition-colors">
              <ChevronLeft size={13} /> Back to Chat
            </Link>
            <div className="flex items-center gap-2">
              <Shield size={14} className="text-primary" />
              <span className="text-sm font-medium">Admin</span>
            </div>
          </div>
        <nav className="p-2 space-y-0.5">
          {[
            { id: 'users', icon: Users, label: 'Users' },
            { id: 'models', icon: Cpu, label: 'AI Models' },
            { id: 'datasources', icon: Database, label: 'Datasources' },
            { id: 'mcp', icon: Zap, label: 'MCP Hub' },
            { id: 'skills', icon: Zap, label: 'Skills' },
            { id: 'audit', icon: FileText, label: 'Audit Logs' },
          ].map(({ id, icon: Icon, label }) => (
            <button
              key={id}
              onClick={() => setTab(id as AdminTab)}
              className={cn(
                'flex items-center gap-2 w-full rounded-lg px-2 py-1.5 text-sm transition-colors',
                tab === id ? 'bg-accent text-foreground' : 'text-muted-foreground hover:text-foreground hover:bg-accent/50'
              )}
            >
              <Icon size={13} />
              {label}
            </button>
          ))}
        </nav>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto p-6">
        {tab === 'users' && <UsersPanel />}
        {tab === 'models' && <ModelsPanel />}
        {tab === 'datasources' && <DatasourcesPanel />}
        {tab === 'mcp' && <MCPPanel />}
        {tab === 'skills' && <SkillsPanel />}
        {tab === 'audit' && <AuditPanel />}
      </div>
    </div>
  )
}

function PermissionsModal({ userId, username, onClose }: { userId: string; username: string; onClose: () => void }) {
  const [datasources, setDatasources] = useState<{ id: string; name: string }[]>([])
  const [models, setModels] = useState<{ id: string; name: string }[]>([])
  const [skills, setSkills] = useState<{ id: string; name: string }[]>([])
  const [knowledge, setKnowledge] = useState<{ id: string; name: string }[]>([])
  const [mcpServers, setMcpServers] = useState<{ id: string; name: string }[]>([])
  const [selected, setSelected] = useState<{
    datasources: string[]
    models: string[]
    skills: string[]
    knowledge: string[]
    mcp_servers: string[]
  }>({ datasources: [], models: [], skills: [], knowledge: [], mcp_servers: [] })
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    Promise.all([
      datasourcesApi.list(),
      modelsApi.list(),
      skillsApi.list(),
      knowledgeApi.list(),
      mcpApi.list(),
      adminApi.getPermissions(userId),
    ]).then(([ds, ms, ss, ks, mcps, perms]) => {
      setDatasources(ds)
      setModels(ms)
      setSkills(ss)
      setKnowledge(ks)
      setMcpServers(mcps)
      setSelected({
        datasources: perms.datasources || [],
        models: perms.models || [],
        skills: perms.skills || [],
        knowledge: perms.knowledge || [],
        mcp_servers: perms.mcp_servers || [],
      })
    })
  }, [userId])

  const toggle = (category: keyof typeof selected, id: string) => {
    setSelected((prev) => ({
      ...prev,
      [category]: prev[category].includes(id)
        ? prev[category].filter((x) => x !== id)
        : [...prev[category], id],
    }))
  }

  const save = async () => {
    setSaving(true)
    try {
      await adminApi.assignPermissions(userId, {
        datasources: selected.datasources.map((id) => ({ datasource_id: id })),
        models: selected.models,
        skills: selected.skills,
        knowledge: selected.knowledge,
        mcp_servers: selected.mcp_servers.map((id) => ({ mcp_id: id })),
      })
      onClose()
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
      <div className="bg-card border border-border rounded-xl p-6 w-[560px] max-h-[80vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-base font-semibold">Permissions — {username}</h3>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground"><X size={16} /></button>
        </div>
        <p className="text-xs text-muted-foreground mb-4">Select resources this user can access. This replaces all existing permissions.</p>

        {[
          { label: 'AI Models', key: 'models' as const, items: models },
          { label: 'Datasources', key: 'datasources' as const, items: datasources },
          { label: 'Tools (MCP Servers)', key: 'mcp_servers' as const, items: mcpServers },
          { label: 'Skills', key: 'skills' as const, items: skills },
          { label: 'Knowledge Sources', key: 'knowledge' as const, items: knowledge },
        ].map(({ label, key, items }) => (
          <div key={key} className="mb-4">
            <div className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">{label}</div>
            {items.length === 0 ? (
              <p className="text-xs text-muted-foreground italic">None configured</p>
            ) : (
              <div className="grid grid-cols-2 gap-1.5">
                {items.map((item) => (
                  <label key={item.id} className="flex items-center gap-2 text-sm cursor-pointer rounded-lg px-2 py-1.5 hover:bg-muted/50">
                    <input
                      type="checkbox"
                      checked={selected[key].includes(item.id)}
                      onChange={() => toggle(key, item.id)}
                      className="accent-primary"
                    />
                    <span className="truncate">{item.name}</span>
                  </label>
                ))}
              </div>
            )}
          </div>
        ))}

        <div className="flex gap-2 mt-4 pt-4 border-t border-border">
          <button onClick={save} disabled={saving} className="bg-primary text-primary-foreground rounded-lg px-4 py-1.5 text-sm disabled:opacity-50">
            {saving ? 'Saving...' : 'Save Permissions'}
          </button>
          <button onClick={onClose} className="bg-muted rounded-lg px-4 py-1.5 text-sm text-muted-foreground">Cancel</button>
        </div>
      </div>
    </div>
  )
}

function UsersPanel() {
  const [users, setUsers] = useState<{ id: string; email: string; username: string; role: string; is_active: boolean }[]>([])
  const [showCreate, setShowCreate] = useState(false)
  const [permUserId, setPermUserId] = useState<string | null>(null)
  const [form, setForm] = useState({ email: '', username: '', password: '', role: 'user' })

  useEffect(() => { adminApi.listUsers().then(setUsers) }, [])

  const createUser = async () => {
    await adminApi.createUser(form)
    const updated = await adminApi.listUsers()
    setUsers(updated)
    setShowCreate(false)
    setForm({ email: '', username: '', password: '', role: 'user' })
  }

  const toggleActive = async (u: { id: string; is_active: boolean }) => {
    await adminApi.updateUser(u.id, { is_active: !u.is_active })
    setUsers((prev) => prev.map((x) => x.id === u.id ? { ...x, is_active: !u.is_active } : x))
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold">Users</h2>
        <button onClick={() => setShowCreate(!showCreate)} className="bg-primary text-primary-foreground rounded-lg px-3 py-1.5 text-sm">
          Add User
        </button>
      </div>

      {showCreate && (
        <div className="bg-card border border-border rounded-xl p-4 mb-4 space-y-3">
          <h3 className="text-sm font-medium">Create User</h3>
          {['email', 'username', 'password'].map((f) => (
            <input
              key={f}
              type={f === 'password' ? 'password' : 'text'}
              placeholder={f.charAt(0).toUpperCase() + f.slice(1)}
              value={(form as Record<string, string>)[f]}
              onChange={(e) => setForm({ ...form, [f]: e.target.value })}
              className="w-full bg-muted border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50"
            />
          ))}
          <select
            value={form.role}
            onChange={(e) => setForm({ ...form, role: e.target.value })}
            className="w-full bg-muted border border-border rounded-lg px-3 py-2 text-sm"
          >
            <option value="user">User</option>
            <option value="admin">Admin</option>
          </select>
          <div className="flex gap-2">
            <button onClick={createUser} className="bg-primary text-primary-foreground rounded-lg px-3 py-1.5 text-sm">Create</button>
            <button onClick={() => setShowCreate(false)} className="bg-muted rounded-lg px-3 py-1.5 text-sm text-muted-foreground">Cancel</button>
          </div>
        </div>
      )}

      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border">
              {['Username', 'Email', 'Role', 'Status', 'Actions'].map((h) => (
                <th key={h} className="text-left px-4 py-3 text-muted-foreground font-medium">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} className="border-b border-border/50 hover:bg-muted/20">
                <td className="px-4 py-3">{u.username}</td>
                <td className="px-4 py-3 text-muted-foreground">{u.email}</td>
                <td className="px-4 py-3">
                  <span className={cn('text-xs px-2 py-0.5 rounded-full', u.role === 'admin' ? 'bg-primary/20 text-primary' : 'bg-muted text-muted-foreground')}>
                    {u.role}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <span className={cn('text-xs px-2 py-0.5 rounded-full', u.is_active ? 'bg-green-500/20 text-green-400' : 'bg-destructive/20 text-destructive')}>
                    {u.is_active ? 'Active' : 'Disabled'}
                  </span>
                </td>
                <td className="px-4 py-3 flex gap-3">
                  <button onClick={() => toggleActive(u)} className="text-xs text-muted-foreground hover:text-foreground">
                    {u.is_active ? 'Disable' : 'Enable'}
                  </button>
                  <button onClick={() => setPermUserId(u.id)} className="text-xs text-primary hover:underline">
                    Permissions
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {permUserId && (
        <PermissionsModal
          userId={permUserId}
          username={users.find((u) => u.id === permUserId)?.username || ''}
          onClose={() => setPermUserId(null)}
        />
      )}
    </div>
  )
}

function ModelsPanel() {
  const [models, setModels] = useState<{ id: string; name: string; provider: string; model_id: string; is_default: boolean }[]>([])
  const [showCreate, setShowCreate] = useState(false)
  const [form, setForm] = useState({ name: '', provider: 'anthropic', model_id: 'claude-sonnet-4-6', api_key: '', base_url: '', is_default: false })
  const [testing, setTesting] = useState<string | null>(null)

  useEffect(() => { modelsApi.list().then(setModels) }, [])

  const createModel = async () => {
    await modelsApi.create(form)
    const updated = await modelsApi.list()
    setModels(updated)
    setShowCreate(false)
  }

  const testModel = async (id: string) => {
    setTesting(id)
    const result = await modelsApi.test(id)
    alert(result.success ? `✓ ${result.response}` : `✗ ${result.error}`)
    setTesting(null)
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold">AI Models</h2>
        <button onClick={() => setShowCreate(!showCreate)} className="bg-primary text-primary-foreground rounded-lg px-3 py-1.5 text-sm">
          Add Model
        </button>
      </div>

      {showCreate && (
        <div className="bg-card border border-border rounded-xl p-4 mb-4 space-y-3">
          <h3 className="text-sm font-medium">Add Model</h3>
          <input placeholder="Display Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}
            className="w-full bg-muted border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50" />
          <select value={form.provider} onChange={(e) => setForm({ ...form, provider: e.target.value })}
            className="w-full bg-muted border border-border rounded-lg px-3 py-2 text-sm">
            <option value="anthropic">Anthropic</option>
            <option value="openai">OpenAI</option>
            <option value="custom">Custom (OpenAI-compatible)</option>
          </select>
          <input placeholder="Model ID (e.g. claude-sonnet-4-6)" value={form.model_id} onChange={(e) => setForm({ ...form, model_id: e.target.value })}
            className="w-full bg-muted border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50" />
          <input placeholder="API Key" type="password" value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })}
            className="w-full bg-muted border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50" />
          <input placeholder="Base URL (for proxy/custom)" value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })}
            className="w-full bg-muted border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50" />
          <div className="flex gap-2">
            <button onClick={createModel} className="bg-primary text-primary-foreground rounded-lg px-3 py-1.5 text-sm">Save</button>
            <button onClick={() => setShowCreate(false)} className="bg-muted rounded-lg px-3 py-1.5 text-sm text-muted-foreground">Cancel</button>
          </div>
        </div>
      )}

      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border">
              {['Name', 'Provider', 'Model ID', 'Actions'].map((h) => (
                <th key={h} className="text-left px-4 py-3 text-muted-foreground font-medium">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {models.map((m) => (
              <tr key={m.id} className="border-b border-border/50 hover:bg-muted/20">
                <td className="px-4 py-3 flex items-center gap-2">
                  {m.name}
                  {m.is_default && <span className="text-xs bg-primary/20 text-primary px-1.5 py-0.5 rounded">default</span>}
                </td>
                <td className="px-4 py-3 text-muted-foreground">{m.provider}</td>
                <td className="px-4 py-3 font-mono text-xs text-muted-foreground">{m.model_id}</td>
                <td className="px-4 py-3 flex gap-2">
                  <button onClick={() => testModel(m.id)} disabled={testing === m.id}
                    className="text-xs text-primary hover:underline disabled:opacity-50">
                    {testing === m.id ? 'Testing...' : 'Test'}
                  </button>
                  <button onClick={() => { modelsApi.delete(m.id); setModels(prev => prev.filter(x => x.id !== m.id)) }}
                    className="text-xs text-destructive hover:underline">Delete</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function DatasourcesPanel() {
  const [sources, setSources] = useState<{ id: string; name: string; host: string; database_name: string; is_active: boolean }[]>([])
  const [showCreate, setShowCreate] = useState(false)
  const [form, setForm] = useState({ name: '', host: '', port: 3306, database_name: '', username: '', password: '' })

  useEffect(() => { datasourcesApi.list().then(setSources) }, [])

  const createSource = async () => {
    await datasourcesApi.create(form)
    setSources(await datasourcesApi.list())
    setShowCreate(false)
  }

  const testSource = async (id: string) => {
    const result = await datasourcesApi.test(id)
    alert(result.success ? '✓ Connection successful' : '✗ Connection failed')
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold">MySQL Datasources</h2>
        <button onClick={() => setShowCreate(!showCreate)} className="bg-primary text-primary-foreground rounded-lg px-3 py-1.5 text-sm">
          Add Datasource
        </button>
      </div>

      {showCreate && (
        <div className="bg-card border border-border rounded-xl p-4 mb-4 grid grid-cols-2 gap-3">
          <h3 className="text-sm font-medium col-span-2">Add MySQL Datasource</h3>
          {[
            { key: 'name', placeholder: 'Name' },
            { key: 'host', placeholder: 'Host' },
            { key: 'database_name', placeholder: 'Database Name' },
            { key: 'username', placeholder: 'Username' },
            { key: 'password', placeholder: 'Password', type: 'password' },
          ].map(({ key, placeholder, type }) => (
            <input key={key} type={type || 'text'} placeholder={placeholder}
              value={(form as Record<string, unknown>)[key] as string}
              onChange={(e) => setForm({ ...form, [key]: e.target.value })}
              className="bg-muted border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50" />
          ))}
          <div className="col-span-2 flex gap-2">
            <button onClick={createSource} className="bg-primary text-primary-foreground rounded-lg px-3 py-1.5 text-sm">Save</button>
            <button onClick={() => setShowCreate(false)} className="bg-muted rounded-lg px-3 py-1.5 text-sm text-muted-foreground">Cancel</button>
          </div>
        </div>
      )}

      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border">
              {['Name', 'Host', 'Database', 'Actions'].map((h) => (
                <th key={h} className="text-left px-4 py-3 text-muted-foreground font-medium">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sources.map((s) => (
              <tr key={s.id} className="border-b border-border/50 hover:bg-muted/20">
                <td className="px-4 py-3">{s.name}</td>
                <td className="px-4 py-3 font-mono text-xs text-muted-foreground">{s.host}</td>
                <td className="px-4 py-3 text-muted-foreground">{s.database_name}</td>
                <td className="px-4 py-3 flex gap-2">
                  <button onClick={() => testSource(s.id)} className="text-xs text-primary hover:underline">Test</button>
                  <button onClick={() => { datasourcesApi.delete(s.id); setSources(prev => prev.filter(x => x.id !== s.id)) }}
                    className="text-xs text-destructive hover:underline">Delete</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function MCPPanel() {
  const [servers, setServers] = useState<{ id: string; name: string; type: string; builtin_key: string | null }[]>([])
  const [showCreate, setShowCreate] = useState(false)
  const [form, setForm] = useState({ name: '', type: 'builtin', builtin_key: 'mysql' })

  useEffect(() => { mcpApi.list().then(setServers) }, [])

  const createServer = async () => {
    await mcpApi.create(form)
    setServers(await mcpApi.list())
    setShowCreate(false)
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold">MCP Hub</h2>
        <button onClick={() => setShowCreate(!showCreate)} className="bg-primary text-primary-foreground rounded-lg px-3 py-1.5 text-sm">
          Add Server
        </button>
      </div>

      {showCreate && (
        <div className="bg-card border border-border rounded-xl p-4 mb-4 space-y-3">
          <input placeholder="Server Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}
            className="w-full bg-muted border border-border rounded-lg px-3 py-2 text-sm focus:outline-none" />
          <select value={form.builtin_key} onChange={(e) => setForm({ ...form, builtin_key: e.target.value })}
            className="w-full bg-muted border border-border rounded-lg px-3 py-2 text-sm">
            {['mysql', 'log', 'github', 'metrics', 'knowledge'].map((k) => (
              <option key={k} value={k}>{k}</option>
            ))}
          </select>
          <div className="flex gap-2">
            <button onClick={createServer} className="bg-primary text-primary-foreground rounded-lg px-3 py-1.5 text-sm">Save</button>
            <button onClick={() => setShowCreate(false)} className="bg-muted rounded-lg px-3 py-1.5 text-sm text-muted-foreground">Cancel</button>
          </div>
        </div>
      )}

      <div className="grid grid-cols-3 gap-3">
        {servers.map((s) => (
          <div key={s.id} className="bg-card border border-border rounded-xl p-4">
            <div className="flex items-start justify-between">
              <div>
                <div className="text-sm font-medium">{s.name}</div>
                <div className="text-xs text-muted-foreground mt-0.5">{s.builtin_key || 'custom'}</div>
              </div>
              <button onClick={() => { mcpApi.delete(s.id); setServers(prev => prev.filter(x => x.id !== s.id)) }}
                className="text-xs text-destructive hover:underline">Remove</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function SkillsPanel() {
  const [skills, setSkills] = useState<{ id: string; name: string; type: string; description: string | null }[]>([])

  useEffect(() => { skillsApi.list().then(setSkills) }, [])

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold">Skills</h2>
        <p className="text-sm text-muted-foreground">Manage skills in <a href="/skills" className="text-primary hover:underline">SkillHub</a></p>
      </div>
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border">
              {['Name', 'Type', 'Description'].map((h) => (
                <th key={h} className="text-left px-4 py-3 text-muted-foreground font-medium">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {skills.map((s) => (
              <tr key={s.id} className="border-b border-border/50 hover:bg-muted/20">
                <td className="px-4 py-3 font-medium">{s.name}</td>
                <td className="px-4 py-3">
                  <span className="text-xs bg-muted px-2 py-0.5 rounded font-mono">{s.type}</span>
                </td>
                <td className="px-4 py-3 text-muted-foreground truncate max-w-xs">{s.description || '—'}</td>
              </tr>
            ))}
            {skills.length === 0 && (
              <tr><td colSpan={3} className="px-4 py-8 text-center text-muted-foreground">No skills created yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function AuditPanel() {
  const [logs, setLogs] = useState<{ id: number; action: string; user_id: string | null; created_at: string; details_json: object | null }[]>([])

  useEffect(() => { adminApi.auditLogs().then(setLogs) }, [])

  return (
    <div>
      <h2 className="text-lg font-semibold mb-4">Audit Logs</h2>
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border">
              {['Time', 'User', 'Action', 'Details'].map((h) => (
                <th key={h} className="text-left px-4 py-3 text-muted-foreground font-medium">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {logs.map((log) => (
              <tr key={log.id} className="border-b border-border/50 hover:bg-muted/20">
                <td className="px-4 py-2 text-xs text-muted-foreground">{new Date(log.created_at).toLocaleString()}</td>
                <td className="px-4 py-2 text-xs">{log.user_id?.slice(0, 8) || 'system'}</td>
                <td className="px-4 py-2 text-xs font-mono">{log.action}</td>
                <td className="px-4 py-2 text-xs text-muted-foreground truncate max-w-xs">{JSON.stringify(log.details_json)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
