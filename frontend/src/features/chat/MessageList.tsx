// Autoscroll that gets out of the way.
//
// Sticky-to-bottom while the reader is at the bottom, but the moment they
// scroll up to re-read something autoscroll releases and a "jump to latest"
// pill appears. Fighting the user's scroll during a long streamed answer is
// the single most irritating bug in chat UIs.

import { useEffect, useRef, useState } from 'react'
import { ArrowDown } from 'lucide-react'

import { Button } from '@/components/ui'
import { CONTENT_COL } from '@/lib/format'
import type { ApprovalDecision, ChatMessage } from '@/lib/types'

import { MessageTurn } from './MessageTurn'

const AT_BOTTOM_SLACK_PX = 80

export function MessageList({
  messages,
  onDecide,
}: {
  messages: ChatMessage[]
  onDecide?: (messageId: string, decision: ApprovalDecision) => void
}) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const [pinned, setPinned] = useState(true)

  useEffect(() => {
    const node = scrollRef.current
    if (!node) return
    const onScroll = () => {
      const distance = node.scrollHeight - node.scrollTop - node.clientHeight
      setPinned(distance < AT_BOTTOM_SLACK_PX)
    }
    node.addEventListener('scroll', onScroll, { passive: true })
    return () => node.removeEventListener('scroll', onScroll)
  }, [])

  // Depends on the streamed content, so it runs per token while pinned.
  const signature = messages
    ?.map((m) => `${m.id}:${m.content.length}:${m.toolCalls.length}`)
    .join('|')
  useEffect(() => {
    if (!pinned) return
    const node = scrollRef.current
    if (node) node.scrollTop = node.scrollHeight
  }, [signature, pinned])

  return (
    /* `min-w-0` is load-bearing and has no visual signature in an empty chat.
       This is a grid item, so it carries `min-width: auto` — the automatic
       minimum size — and without the override it refuses to shrink below the
       min-content width of its widest descendant. One 8-column tool result is
       enough to push the entire page into a horizontal scroll on a phone,
       measured at 300px before this was added. */
    <div className="relative min-h-0 min-w-0">
      <div ref={scrollRef} className="scrollbar-thin h-full overflow-y-auto px-4 py-5 sm:px-6">
        <div className={`${CONTENT_COL} flex flex-col gap-6`}>
          {messages?.map((message) => (
            <MessageTurn key={message.id} message={message} onDecide={onDecide} />
          ))}
        </div>
      </div>

      {!pinned && (
        <Button
          variant="outline"
          size="sm"
          onClick={() => setPinned(true)}
          className="animate-rise absolute bottom-3 left-1/2 -translate-x-1/2 rounded-full shadow-menu"
        >
          <ArrowDown className="size-3" aria-hidden="true" />
          jump to latest
        </Button>
      )}
    </div>
  )
}
