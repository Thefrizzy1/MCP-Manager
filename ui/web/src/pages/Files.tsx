import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { File, Folder, Database, ChevronUp } from 'lucide-react'
import { api } from '@/lib/api'
import { fmtSize, fmtTime } from '@/lib/format'
import { PageHead, PageBody } from '@/components/PageHead'
import { Card } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'

interface Item {
  name: string
  path: string
  type: 'dir' | 'file'
  size?: number
  mtime?: number
  kind?: string
  exists?: boolean
}
interface Listing {
  path: string
  parent?: string | null
  items: Item[]
  is_root?: boolean
}

export function Files() {
  const [path, setPath] = useState('')
  const [preview, setPreview] = useState<{ name: string; text: string } | null>(null)
  const { data, isLoading, refetch } = useQuery({
    queryKey: ['files', path],
    queryFn: () => api.get<Listing>(`/api/v1/files/list?path=${encodeURIComponent(path)}`),
  })

  async function open(it: Item) {
    if (it.type === 'dir') {
      setPreview(null)
      setPath(it.path)
    } else {
      try {
        const d = await api.get<{ text?: string }>(`/api/v1/files/read?path=${encodeURIComponent(it.path)}`)
        setPreview({ name: it.name, text: d.text || '(empty)' })
      } catch (e) {
        alert(String(e))
      }
    }
  }

  async function del(it: Item) {
    if (!confirm(`Delete ${it.name}?`)) return
    try {
      await api.post('/api/v1/files/delete', { path: it.path })
      refetch()
    } catch (e) {
      alert(String(e))
    }
  }

  const items = data?.items ?? []

  return (
    <>
      <PageHead title="Files" subtitle="Internal research storage the agents write to, plus mounted shares" />
      <PageBody>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <Button variant="ghost" size="sm" onClick={() => setPath('')}>
            Roots
          </Button>
          {data?.path && <code className="font-mono text-[12px] text-ink-3">{data.path}</code>}
          {data?.parent && (
            <Button variant="ghost" size="sm" onClick={() => setPath(data.parent!)}>
              <ChevronUp size={14} /> Up
            </Button>
          )}
        </div>

        <Card className="overflow-hidden">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="border-b border-border text-left text-[11px] uppercase tracking-wide text-ink-3">
                <th className="px-3 py-2 font-semibold">Name</th>
                <th className="px-3 py-2 font-semibold">Size</th>
                <th className="px-3 py-2 font-semibold">Modified</th>
                <th className="px-3 py-2 text-right font-semibold">Actions</th>
              </tr>
            </thead>
            <tbody>
              {isLoading ? (
                <tr>
                  <td colSpan={4} className="px-3 py-6 text-ink-3">
                    Loading…
                  </td>
                </tr>
              ) : items.length === 0 ? (
                <tr>
                  <td colSpan={4} className="px-3 py-6 text-ink-3">
                    Empty.
                  </td>
                </tr>
              ) : (
                items.map((it) => {
                  const Icon = it.kind === 'internal' ? Database : it.type === 'dir' ? Folder : File
                  return (
                    <tr key={it.path} className="border-b border-border last:border-0 hover:bg-surface-hover">
                      <td className="px-3 py-2.5">
                        <button className="flex items-center gap-2 text-left" onClick={() => open(it)}>
                          <Icon size={15} className="shrink-0 text-ink-3" />
                          <span className="font-medium text-ink">{it.name}</span>
                          {it.exists === false && <span className="text-[11.5px] text-ink-3">(not mounted)</span>}
                        </button>
                      </td>
                      <td className="px-3 py-2.5 text-[12px] text-ink-3">{it.type === 'dir' ? '—' : fmtSize(it.size)}</td>
                      <td className="px-3 py-2.5 text-[12px] text-ink-3">{fmtTime(it.mtime)}</td>
                      <td className="px-3 py-2.5">
                        {it.type === 'file' && (
                          <div className="flex items-center justify-end gap-1">
                            <Button variant="ghost" size="sm" onClick={() => open(it)}>
                              Preview
                            </Button>
                            <a href={`/api/v1/files/download?path=${encodeURIComponent(it.path)}`}>
                              <Button variant="ghost" size="sm">
                                Download
                              </Button>
                            </a>
                            <Button variant="danger" size="sm" onClick={() => del(it)}>
                              Delete
                            </Button>
                          </div>
                        )}
                      </td>
                    </tr>
                  )
                })
              )}
            </tbody>
          </table>
        </Card>

        {preview && (
          <Card className="mt-4">
            <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
              <span className="font-mono text-[12px] text-ink-2">{preview.name}</span>
              <Button variant="ghost" size="sm" onClick={() => setPreview(null)}>
                Close
              </Button>
            </div>
            <pre className="max-h-[50vh] overflow-auto whitespace-pre-wrap px-4 py-3 font-mono text-[12px] text-ink-2">
              {preview.text}
            </pre>
          </Card>
        )}
      </PageBody>
    </>
  )
}
