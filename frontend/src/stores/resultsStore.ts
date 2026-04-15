import { create } from 'zustand'

export interface Artifact {
  id: string
  type: 'table' | 'code' | 'log' | 'chart' | 'text' | 'html' | 'image' | 'file'
  data?: Record<string, unknown>[]  // for table
  content?: string                  // for code/log/text
  language?: string                 // for code
  title?: string
  // image / file artifacts (base64 data from sandbox execution)
  dataB64?: string
  mimeType?: string
  fileName?: string
}

interface ResultsState {
  artifacts: Artifact[]
  activeArtifactId: string | null
  addArtifact: (artifact: Omit<Artifact, 'id'>) => void
  setActiveArtifact: (id: string) => void
  clearArtifacts: () => void
}

export const useResultsStore = create<ResultsState>((set) => ({
  artifacts: [],
  activeArtifactId: null,
  addArtifact: (artifact) => {
    const id = crypto.randomUUID()
    set((s) => ({
      artifacts: [{ ...artifact, id }, ...s.artifacts].slice(0, 20),
      activeArtifactId: id,
    }))
  },
  setActiveArtifact: (id) => set({ activeArtifactId: id }),
  clearArtifacts: () => set({ artifacts: [], activeArtifactId: null }),
}))
