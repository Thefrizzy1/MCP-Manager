import { Hammer } from 'lucide-react'
import { NAV_GROUPS, SETTINGS_ITEM } from '@/lib/nav'
import { PageHead, PageBody } from '@/components/PageHead'
import { Card } from '@/components/ui/Card'

function labelFor(route: string): string {
  const all = [...NAV_GROUPS.flatMap((g) => g.items), SETTINGS_ITEM]
  return all.find((i) => i.id === route)?.label ?? 'Plutus'
}

export function Placeholder({ route }: { route: string }) {
  const label = labelFor(route)
  return (
    <>
      <PageHead title={label} />
      <PageBody>
        <Card className="flex flex-col items-center justify-center gap-3 px-6 py-16 text-center">
          <div className="flex h-11 w-11 items-center justify-center rounded-[var(--radius)] bg-surface-2 text-ink-3">
            <Hammer size={20} />
          </div>
          <div className="max-w-sm">
            <h3 className="text-[15px] font-semibold text-ink">{label} is being rebuilt</h3>
            <p className="mt-1 text-[13px] text-ink-3">
              This screen is moving to the new interface. The backend still works — the view lands here shortly.
            </p>
          </div>
        </Card>
      </PageBody>
    </>
  )
}
