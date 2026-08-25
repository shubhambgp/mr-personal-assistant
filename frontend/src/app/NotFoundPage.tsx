// The two "there is nothing here" states, told apart because they mean
// different things to a rep: a mistyped URL is a navigation slip, while a
// dead conversation link usually means "you deleted this chat on another
// device" — and each deserves its own sentence.
//
// The signed-out variant of this problem does not exist: unknown paths for a
// signed-out visitor still redirect to /login (App's top-level Routes), which
// deliberately reveals nothing about what exists.

import { useNavigate } from 'react-router-dom'
import { MessageSquareOff, Compass } from 'lucide-react'

import { Button } from '@/components/ui'
import { ROUTES } from '@/lib/routes'

const COPY = {
  page: {
    Icon: Compass,
    title: 'This page does not exist',
    body: 'The address may be mistyped, or the link is out of date.',
  },
  conversation: {
    Icon: MessageSquareOff,
    title: 'Conversation not found',
    body: 'This conversation does not exist or was deleted. Your other conversations are untouched.',
  },
} as const

export function NotFoundPage({
  kind,
  /** `page` renders full-viewport (it replaces the whole shell); `conversation`
   *  renders inside <main>, which already constrains it. */
  fullPage = false,
}: {
  kind: keyof typeof COPY
  fullPage?: boolean
}) {
  const navigate = useNavigate()
  const { Icon, title, body } = COPY[kind]

  return (
    <div className={`flex items-center justify-center bg-page p-6 ${fullPage ? 'min-h-dvh' : ''}`}>
      <div className="w-full max-w-sm rounded-card border border-line bg-surface p-6 text-center shadow-lift">
        <Icon className="mx-auto size-6 text-fg-subtle" aria-hidden="true" />
        <h1 className="mt-3 font-serif text-xl text-fg">{title}</h1>
        <p className="mt-2 text-xs text-fg-muted">{body}</p>
        <Button
          variant="primary"
          size="lg"
          className="mt-5"
          onClick={() => void navigate(ROUTES.assistant)}
        >
          Back to chat
        </Button>
      </div>
    </div>
  )
}
