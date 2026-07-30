import { useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { File, Folder, Database, ChevronUp, Download, Upload, FolderPlus } from 'lucide-react'
import { api } from '@/lib/api'
import { fmtSize, fmtTime } from '@/lib/format'
import { PageHead, PageBody } from '@/components/PageHead'
import { Card } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { useToast } from '@/components/ui/Toast'

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
  const toast = useToast()
  const [path, setPath] = useState('')
  const [preview, setPreview] = useState<{ name: string; text: string } | null>(null)
  const [uploading, setUploading] = useState(false)
  const fileInput = useRef<HTMLInputElement | null>(null)
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
        toast.error(String(e))
      }
    }
  }

  // A folder takes everything in it, so that is asked for explicitly rather than
  // implied by the same confirm as a single file.
  async function del(it: Item) {
    const isDir = it.type === 'dir'
    const question = isDir
      ? `Delete the folder “${it.name}” and everything inside it?`
      : `Delete ${it.name}?`
    if (!confirm(question)) return
    try {
      await api.post('/api/v1/files/delete', { path: it.path, recursive: isDir })
      refetch()
    } catch (e) {
      toast.error(String(e))
    }
  }

  async function mkdir() {
    const name = prompt('New folder name')?.trim()
    if (!name || !data?.path) return
    try {
      await api.post('/api/v1/files/mkdir', { path: data.path, name })
      refetch()
    } catch (e) {
      toast.error(String(e))
    }
  }

  async function upload(files: FileList | null) {
    if (!files?.length || !data?.path) return
    const body = new FormData()
    body.append('path', data.path)
    for (const f of files) body.append('file', f)
    setUploading(true)
    try {
      const r = await fetch('/api/v1/files/upload', { method: 'POST', body })
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText)
      toast.success(`Uploaded ${files.length} file${files.length > 1 ? 's' : ''}.`)
      refetch()
    } catch (e) {
      toast.error(String(e))
    } finally {
      setUploading(false)
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  const items = data?.items ?? []
  const folderZip = (p: string) => `/api/v1/files/download-folder?path=${encodeURIComponent(p)}`

  return (
    <>
      <PageHead
        title="Files"
        subtitle="The research library your agents write into, plus mounted shares"
      />
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
          {data?.path && (
            <div className="ml-auto flex items-center gap-2">
              <input
                ref={fileInput}
                type="file"
                multiple
                className="hidden"
                onChange={(e) => upload(e.target.files)}
              />
              <Button variant="ghost" size="sm" onClick={mkdir}>
                <FolderPlus size={13} /> New folder
              </Button>
              <Button
                variant="ghost"
                size="sm"
                disabled={uploading}
                onClick={() => fileInput.current?.click()}
              >
                <Upload size={13} /> {uploading ? 'Uploading…' : 'Upload'}
              </Button>
              {/* A whole researched structure — notes, subfolders, a dashboard —
                  is not something you get out one file at a time. */}
              <a href={folderZip(data.path)}>
                <Button variant="default" size="sm">
                  <Download size={13} /> Download folder
                </Button>
              </a>
            </div>
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
                        <div className="flex items-center justify-end gap-1">
                          {it.type === 'file' ? (
                            <>
                              <Button variant="ghost" size="sm" onClick={() => open(it)}>
                                Preview
                              </Button>
                              <a href={`/api/v1/files/download?path=${encodeURIComponent(it.path)}`}>
                                <Button variant="ghost" size="sm">
                                  <Download size={13} /> Download
                                </Button>
                              </a>
                              <Button variant="danger" size="sm" onClick={() => del(it)}>
                                Delete
                              </Button>
                            </>
                          ) : (
                            it.exists !== false && (
                              <>
                                <a href={folderZip(it.path)}>
                                  <Button variant="ghost" size="sm" title="Download as a zip">
                                    <Download size={13} /> Zip
                                  </Button>
                                </a>
                                {/* Root folders are not deletable — the API
                                    refuses them, so the button is not offered. */}
                                {it.kind !== 'internal' && !data?.is_root && (
                                  <Button variant="danger" size="sm" onClick={() => del(it)}>
                                    Delete
                                  </Button>
                                )}
                              </>
                            )
                          )}
                        </div>
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
