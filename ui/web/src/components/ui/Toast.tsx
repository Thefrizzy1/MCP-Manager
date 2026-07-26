import { createContext, useCallback, useContext, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { AlertCircle, CheckCircle2, Info, X } from 'lucide-react'
import { cn } from '@/lib/cn'

type ToastKind = 'error' | 'success' | 'info'
interface ToastItem {
  id: number
  kind: ToastKind
  msg: string
}
interface ToastApi {
  error: (msg: string) => void
  success: (msg: string) => void
  info: (msg: string) => void
}

const Ctx = createContext<ToastApi | null>(null)

const STYLE: Record<ToastKind, string> = {
  error: 'border-danger/40 bg-danger-weak text-danger',
  success: 'border-ok/40 bg-ok-weak text-ok',
  info: 'border-border bg-surface text-ink',
}
const ICON = { error: AlertCircle, success: CheckCircle2, info: Info }

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([])

  const remove = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), [])
  const push = useCallback(
    (kind: ToastKind, msg: string) => {
      const id = Date.now() + Math.random()
      setToasts((t) => [...t.slice(-3), { id, kind, msg }])
      setTimeout(() => remove(id), 5000)
    },
    [remove],
  )

  const api: ToastApi = {
    error: (m) => push('error', m),
    success: (m) => push('success', m),
    info: (m) => push('info', m),
  }

  return (
    <Ctx.Provider value={api}>
      {children}
      {createPortal(
        <div className="fixed bottom-4 right-4 z-[60] flex w-80 max-w-[calc(100vw-2rem)] flex-col gap-2">
          {toasts.map((t) => {
            const Icon = ICON[t.kind]
            return (
              <div
                key={t.id}
                role="status"
                className={cn(
                  'flex items-start gap-2 rounded-[var(--radius)] border px-3 py-2.5 text-[12.5px] shadow-[var(--shadow-pop)]',
                  STYLE[t.kind],
                )}
              >
                <Icon size={15} className="mt-0.5 shrink-0" />
                <span className="min-w-0 flex-1 break-words">{t.msg}</span>
                <button onClick={() => remove(t.id)} aria-label="Dismiss" className="shrink-0 opacity-70 hover:opacity-100">
                  <X size={14} />
                </button>
              </div>
            )
          })}
        </div>,
        document.body,
      )}
    </Ctx.Provider>
  )
}

export function useToast(): ToastApi {
  const c = useContext(Ctx)
  return (
    c ?? {
      error: (m) => console.error(m),
      success: () => {},
      info: () => {},
    }
  )
}
