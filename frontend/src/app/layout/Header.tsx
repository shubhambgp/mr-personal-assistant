import { PanelLeft } from 'lucide-react'

import { IconButton } from '@/components/ui'

export function Header({
  repName,
  repCode,
  vintage,
  onOpenSidebar,
}: {
  repName: string
  repCode: number
  vintage: string
  onOpenSidebar: () => void
}) {
  return (
    <header className="flex min-w-0 items-center gap-2 border-b border-line bg-page/85 px-3 py-2 backdrop-blur-sm sm:px-5">
      <IconButton
        label="Open conversations"
        onClick={onOpenSidebar}
        className="lg:hidden"
      >
        <PanelLeft className="size-4" aria-hidden="true" />
      </IconButton>

      <div className="min-w-0 flex-1">
        <h1 className="truncate text-sm font-semibold text-fg">MR Personal Assistant</h1>
        {/* Secondary line is dropped below sm: at 375px the title alone is
            already close to the available width. */}
        <p className="hidden truncate text-2xs text-fg-subtle sm:block">
          {repName} · {repCode}
          {vintage && <> · data as of {vintage}</>}
        </p>
      </div>
      {/* Sign-out moved into the sidebar's Settings menu, with the other
          account-level actions. */}
    </header>
  )
}
