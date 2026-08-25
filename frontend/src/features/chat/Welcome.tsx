import { CalendarClock, Gauge, Stethoscope, Users } from 'lucide-react'

import { CONTENT_COL } from '@/lib/format'

const STARTERS = [
  { label: 'Today’s plan', prompt: 'What should I focus on today?', Icon: CalendarClock },
  {
    label: 'Pending visits',
    prompt: 'Which of my visits are pending this month?',
    Icon: Stethoscope,
  },
  { label: 'My scorecard', prompt: 'How am I doing against my targets this month?', Icon: Gauge },
  {
    label: 'Doctors by specialty',
    prompt: 'How many doctors do I cover, by specialty?',
    Icon: Users,
  },
]

export function Welcome({ name, onPick }: { name: string; onPick: (prompt: string) => void }) {
  const firstName = name.trim().split(/\s+/)[0] ?? 'there'

  return (
    <div className="scrollbar-thin flex h-full min-w-0 items-center justify-center overflow-y-auto px-4 py-8">
      <div className={`${CONTENT_COL} max-w-2xl text-center`}>
        <h2 className="font-serif text-2xl text-fg">Good to see you, {firstName}</h2>
        <p className="mx-auto mt-2 max-w-md text-sm text-fg-muted">
          Ask about your doctors, visits or performance — or attach a photo of a prescription, RCPA
          sheet or stock board and I’ll read it.
        </p>

        {/* One column on a phone: two 170px cards side by side truncate their
            own prompt text, which is the only useful thing on them. */}
        <div className="mt-7 grid gap-2 sm:grid-cols-2">
          {STARTERS?.map(({ label, prompt, Icon }, i) => (
            <button
              key={label}
              type="button"
              onClick={() => onPick(prompt)}
              className="animate-rise group rounded-card border border-line bg-surface p-3 text-left transition-colors hover:border-accent/40 hover:bg-accent-soft/60"
              style={{ animationDelay: `${i * 45}ms` }}
            >
              <p className="flex items-center gap-2 text-sm font-medium text-fg">
                <Icon className="size-4 shrink-0 text-accent-ink" aria-hidden="true" />
                {label}
              </p>
              <p className="mt-1 text-2xs text-fg-subtle">{prompt}</p>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
