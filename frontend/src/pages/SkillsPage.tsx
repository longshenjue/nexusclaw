import { useState, useEffect } from 'react'
import { skillsApi } from '../api/client'
import { useAuthStore } from '../stores/authStore'
import { cn } from '../lib/utils'
import { Zap, Plus, X } from 'lucide-react'

interface Skill {
  id: string
  name: string
  description: string
  category: string | null
  type: string
  is_public: boolean
  parameters_schema: Record<string, unknown>
}

export function SkillsPage() {
  const { user } = useAuthStore()
  const [skills, setSkills] = useState<Skill[]>([])
  const [showCreate, setShowCreate] = useState(false)
  const [form, setForm] = useState({
    name: '',
    description: '',
    category: '',
    type: 'system_prompt',
    system_prompt: '',
    is_public: false,
  })

  useEffect(() => { skillsApi.list().then(setSkills) }, [])

  const createSkill = async () => {
    await skillsApi.create(form)
    setSkills(await skillsApi.list())
    setShowCreate(false)
  }

  const deleteSkill = async (id: string) => {
    await skillsApi.delete(id)
    setSkills((prev) => prev.filter((s) => s.id !== id))
  }

  const categories = [...new Set(skills.map((s) => s.category).filter(Boolean))]

  return (
    <div className="min-h-screen bg-background p-6">
      <div className="max-w-5xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-semibold flex items-center gap-2">
              <Zap size={22} className="text-primary" />
              Skill Hub
            </h1>
            <p className="text-muted-foreground text-sm mt-1">
              Pre-built and custom troubleshooting workflows
            </p>
          </div>
          <button
            onClick={() => setShowCreate(!showCreate)}
            className="flex items-center gap-2 bg-primary text-primary-foreground rounded-lg px-4 py-2 text-sm"
          >
            <Plus size={14} />
            Create Skill
          </button>
        </div>

        {showCreate && (
          <div className="bg-card border border-border rounded-xl p-5 mb-6 space-y-4">
            <h3 className="text-sm font-medium">New Skill</h3>
            <div className="grid grid-cols-2 gap-3">
              <input placeholder="Skill Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}
                className="bg-muted border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50" />
              <input placeholder="Category (optional)" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}
                className="bg-muted border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50" />
            </div>
            <textarea placeholder="Description" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })}
              className="w-full bg-muted border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50 resize-none" rows={2} />
            <select value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })}
              className="w-full bg-muted border border-border rounded-lg px-3 py-2 text-sm">
              <option value="system_prompt">System Prompt (Persona)</option>
              <option value="workflow">Workflow (Multi-step)</option>
            </select>
            {form.type === 'system_prompt' && (
              <textarea
                placeholder="System prompt - this will be prepended to the conversation to give the AI a specific focus or expertise..."
                value={form.system_prompt}
                onChange={(e) => setForm({ ...form, system_prompt: e.target.value })}
                className="w-full bg-muted border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50 resize-none font-mono"
                rows={6}
              />
            )}
            {user?.role === 'admin' && (
              <label className="flex items-center gap-2 text-sm text-muted-foreground cursor-pointer">
                <input type="checkbox" checked={form.is_public} onChange={(e) => setForm({ ...form, is_public: e.target.checked })}
                  className="rounded" />
                Make public (all users can use this skill)
              </label>
            )}
            <div className="flex gap-2">
              <button onClick={createSkill} className="bg-primary text-primary-foreground rounded-lg px-4 py-1.5 text-sm">Save</button>
              <button onClick={() => setShowCreate(false)} className="bg-muted rounded-lg px-4 py-1.5 text-sm text-muted-foreground">Cancel</button>
            </div>
          </div>
        )}

        {/* Skills grid */}
        {categories.map((cat) => (
          <div key={cat} className="mb-6">
            <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-3">{cat}</h3>
            <div className="grid grid-cols-3 gap-3">
              {skills.filter((s) => s.category === cat).map((skill) => (
                <SkillCard key={skill.id} skill={skill} onDelete={deleteSkill} />
              ))}
            </div>
          </div>
        ))}

        {skills.filter((s) => !s.category).length > 0 && (
          <div>
            <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-3">General</h3>
            <div className="grid grid-cols-3 gap-3">
              {skills.filter((s) => !s.category).map((skill) => (
                <SkillCard key={skill.id} skill={skill} onDelete={deleteSkill} />
              ))}
            </div>
          </div>
        )}

        {skills.length === 0 && (
          <div className="text-center py-16 text-muted-foreground">
            <Zap size={32} className="mx-auto mb-3 opacity-30" />
            <p>No skills yet. Create your first skill to automate troubleshooting workflows.</p>
          </div>
        )}
      </div>
    </div>
  )
}

function SkillCard({ skill, onDelete }: { skill: Skill; onDelete: (id: string) => void }) {
  return (
    <div className="bg-card border border-border rounded-xl p-4 group hover:border-primary/30 transition-colors">
      <div className="flex items-start justify-between mb-2">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-lg bg-primary/10 flex items-center justify-center">
            <Zap size={13} className="text-primary" />
          </div>
          <span className="text-sm font-medium">{skill.name}</span>
        </div>
        <button
          onClick={() => onDelete(skill.id)}
          className="opacity-0 group-hover:opacity-100 text-muted-foreground hover:text-destructive transition-all"
        >
          <X size={13} />
        </button>
      </div>
      <p className="text-xs text-muted-foreground line-clamp-2">{skill.description}</p>
      <div className="flex items-center gap-2 mt-3">
        <span className={cn(
          'text-xs px-1.5 py-0.5 rounded',
          skill.type === 'workflow' ? 'bg-blue-500/20 text-blue-400' : 'bg-purple-500/20 text-purple-400'
        )}>
          {skill.type}
        </span>
        {skill.is_public && (
          <span className="text-xs px-1.5 py-0.5 rounded bg-green-500/20 text-green-400">public</span>
        )}
      </div>
    </div>
  )
}
