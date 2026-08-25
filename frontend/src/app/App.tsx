import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react'
import { matchPath, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'

import { Spinner } from '@/components/ui'
import { useAuth } from '@/features/auth/authContext'
import { LoginPage } from '@/features/auth/LoginPage'
import { useOverdueCount } from '@/features/agenda/useOverdueCount'
import { Composer } from '@/features/chat/Composer'
import { MessageList } from '@/features/chat/MessageList'
import { useChatStream } from '@/features/chat/useChatStream'
import { Welcome } from '@/features/chat/Welcome'
import { Sidebar } from '@/features/conversations/Sidebar'
import { useConversations } from '@/features/conversations/useConversations'
import { api, ApiError } from '@/lib/api'
import { cx } from '@/lib/cx'
import { pathForView, ROUTE_PATTERNS, ROUTES, viewForPath } from '@/lib/routes'
import type { ApprovalDecision, ChatMessage, IngestedDocument, ToolCall } from '@/lib/types'

import { Drawer } from './layout/Drawer'
import { ErrorBoundary, ErrorFallback } from './ErrorBoundary'
import { Header } from './layout/Header'
import { NotFoundPage } from './NotFoundPage'
import { useIsMobile } from './layout/useIsMobile'

// Lazy: the chat is the landing pane; the other three panels ship as separate
// chunks so the first paint (and the login page) does not pay for them.
const AgendaPanel = lazy(() =>
  import('@/features/agenda/AgendaPanel').then((m) => ({ default: m.AgendaPanel })),
)
const SettingsPanel = lazy(() =>
  import('@/features/settings/SettingsPanel').then((m) => ({ default: m.SettingsPanel })),
)
const LibraryPanel = lazy(() =>
  import('@/features/library/LibraryPanel').then((m) => ({ default: m.LibraryPanel })),
)

const SIDEBAR_KEY = 'qorvexa-sidebar'

export default function App() {
  const { rep, loading, logout } = useAuth()

  if (loading) {
    return (
      <div className="flex min-h-dvh items-center justify-center">
        <Spinner className="size-6 text-accent-ink" />
      </div>
    )
  }

  // Auth is the only top-level fork; everything signed-in lives under the
  // catch-all so <Chat> stays MOUNTED across internal navigation — a streaming
  // answer must survive a hop to the Agenda and back.
  return (
    <Routes>
      <Route
        path={ROUTE_PATTERNS.login}
        element={rep ? <Navigate to={ROUTES.assistant} replace /> : <LoginPage />}
      />
      <Route
        path="/*"
        element={
          rep ? (
            <Chat onLogout={logout} repName={rep.rep_name} repCode={rep.rep_code} />
          ) : (
            <Navigate to={ROUTES.login} replace />
          )
        }
      />
    </Routes>
  )
}

function Chat({
  onLogout,
  repName,
  repCode,
}: {
  onLogout: () => Promise<void>
  repName: string
  repCode: number
}) {
  const navigate = useNavigate()
  const location = useLocation()

  // The URL is the routing state: which pane is open, and which conversation.
  // All paths come from lib/routes.ts — nothing here hard-codes a URL.
  const view = viewForPath(location.pathname)
  const conversationMatch = matchPath(ROUTE_PATTERNS.conversation, location.pathname)
  const routeConversationId = conversationMatch?.params.conversationId ?? null

  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [vintage, setVintage] = useState('')
  // The conversation id the server answered 404 for. Never cleared by hand:
  // the render check compares it against the CURRENT route id, so navigating
  // anywhere else makes it inert automatically, and navigating back to the
  // same dead link correctly shows the state again.
  const [missingConversation, setMissingConversation] = useState<string | null>(null)
  const isMobile = useIsMobile()

  // Which conversation's messages are on screen — set by a route load OR by the
  // server naming a brand-new thread mid-stream. A ref, not state: it must be
  // readable inside callbacks without re-binding them per message.
  const loadedRef = useRef<string | null>(null)

  // Keyed on `view` so leaving the Agenda panel after editing tasks
  // refreshes the badge, without polling.
  const overdueCount = useOverdueCount(view)

  // Desktop-only icon rail, remembered per browser. try/catch because storage
  // access itself can throw in private windows (same pattern as lib/theme.ts).
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(SIDEBAR_KEY) === 'rail'
    } catch {
      return false
    }
  })
  const toggleCollapsed = useCallback(() => {
    setCollapsed((v) => {
      try {
        localStorage.setItem(SIDEBAR_KEY, v ? 'open' : 'rail')
      } catch {
        /* private mode */
      }
      return !v
    })
  }, [])

  // Crossing mobile -> desktop with the drawer open would otherwise leave the
  // main column inert forever: the drawer becomes static layout at lg, so its
  // onClose can no longer be reached. setState lives in the media-query change
  // callback, not the effect body (react-hooks/set-state-in-effect).
  useEffect(() => {
    const mq = window.matchMedia('(min-width: 1024px)')
    const onChange = () => {
      if (mq.matches) setSidebarOpen(false)
    }
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  const {
    conversations,
    loading: listLoading,
    error: listError,
    refresh,
    rename,
    remove,
  } = useConversations()

  // The server names a brand-new thread on the first turn: reflect it in the
  // URL (replace, not push — it is the same page) and mark it live so the
  // loader effect below does not refetch what is already streaming on screen.
  const onConversationId = useCallback(
    (id: string) => {
      loadedRef.current = id
      void navigate(ROUTES.conversation(id), { replace: true })
    },
    [navigate],
  )

  const { messages, streaming, send, resume, stop, reset } = useChatStream(onConversationId)

  useEffect(() => {
    let alive = true
    api
      .vintage()
      .then((v) => {
        if (alive) setVintage(v.summary)
      })
      .catch(() => {
        if (alive) setVintage('')
      })
    return () => {
      alive = false
    }
  }, [])

  // The URL drives which conversation is loaded. loadedRef is only advanced
  // once a load actually lands, so StrictMode's cancelled first pass and a
  // mid-stream `replace` both do the right thing.
  useEffect(() => {
    const id = routeConversationId
    if (id === loadedRef.current) return
    let cancelled = false
    void (async () => {
      if (!id) {
        if (!cancelled) {
          loadedRef.current = null
          reset([])
        }
        return
      }
      try {
        const { messages: raw } = await api.conversation(id)
        if (cancelled) return
        loadedRef.current = id
        // Rebuild the tool timeline from what was persisted, so a resumed
        // thread shows the same intermediate steps the rep originally saw.
        reset(
          raw?.map<ChatMessage>((m) => ({
            id: m.id,
            role: m.role,
            content: m.content,
            notices: [],
            grounding:
              m.role === 'assistant' && m.grounded !== null
                ? { grounded: m.grounded, unverifiedClaims: m.unverified_claims ?? [] }
                : undefined,
            // A turn that stopped to ask still needs asking. Without this the
            // card is lost on reload AND the thread stays wedged behind an
            // interrupt that is still pending in the graph checkpoint.
            pendingApproval: m.pending_approval
              ? {
                  interruptId: m.pending_approval.interrupt_id,
                  calls: m.pending_approval.calls,
                  review: m.pending_approval.review,
                }
              : undefined,
            toolCalls: (m.tool_calls ?? [])?.map<ToolCall>((t, i) => ({
              callId: t.call_id ?? `${m.id}-${i}`,
              name: t.name ?? 'tool',
              input: t.input ?? {},
              output: t.output ?? undefined,
              isError: t.is_error ?? false,
              status: t.is_error ? 'error' : 'done',
            })),
          })),
        )
      } catch (err) {
        if (cancelled) return
        if (err instanceof ApiError && err.status === 404) {
          // Deleted on another device, foreign, or mistyped. The backend
          // answers 404 for all three on purpose (never 403 — see
          // security-invariants §1.8), so this is the complete signal.
          // loadedRef stays null: a message typed from this state starts a
          // fresh conversation rather than resurrecting a dead id.
          setMissingConversation(id)
          loadedRef.current = null
          reset([])
        } else {
          loadedRef.current = id
          reset([])
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [routeConversationId, reset])

  const startNew = useCallback(() => {
    setSidebarOpen(false)
    loadedRef.current = null
    reset([])
    void navigate(ROUTES.assistant)
  }, [navigate, reset])

  /** Back to the chat pane — the current conversation when there is one. */
  const chatPath = () =>
    loadedRef.current ? ROUTES.conversation(loadedRef.current) : ROUTES.assistant

  // Refreshing here, at the point the turn actually ends, rather than from an
  // effect watching `streaming`: the first message names the thread, so the
  // sidebar title is only correct after the turn settles.
  const handleSend = useCallback(
    (message: string, images: File[], documents?: IngestedDocument[]) => {
      // One stream at a time. The Composer disables itself while streaming, but
      // the Agenda panel's "summarise"/"draft a reply" buttons also land here —
      // without this guard they started a second concurrent stream, overwrote
      // the abort handle, and could fork a second conversation (audit finding M-FE5).
      if (streaming) return
      // Typing takes you back to the chat, so a panel is a launcher rather than
      // a dead end.
      if (viewForPath(location.pathname) !== 'chat') void navigate(chatPath())
      void send({
        message,
        images,
        documents,
        conversationId: loadedRef.current,
      }).then(refresh)
    },
    [send, refresh, navigate, location.pathname, streaming],
  )

  const handleDecide = useCallback(
    (messageId: string, decision: ApprovalDecision) => {
      const id = loadedRef.current
      if (!id) return
      void resume(messageId, decision, id).then(refresh)
    },
    [resume, refresh],
  )

  const handleDelete = useCallback(
    (id: string) => {
      if (id === loadedRef.current) startNew()
      void remove(id)
    },
    [startNew, remove],
  )

  // "/" still goes home silently — it is an entry point, not a mistake. Any
  // OTHER unknown path gets a real 404 page: a silent redirect made a broken
  // bookmark look like the app ignoring you.
  const knownPath =
    view !== 'chat' || location.pathname === ROUTES.assistant || conversationMatch !== null
  if (!knownPath) {
    if (location.pathname === '/') return <Navigate to={ROUTES.assistant} replace />
    return <NotFoundPage kind="page" fullPage />
  }

  return (
    /* dvh, not vh: on mobile the URL bar changes the viewport height as you
       scroll, and with vh the composer drifts off-screen. The three-row grid
       replaces a four-link `h-full` chain (html → body → #root → App) whose
       failure mode was silent — break one link and the app still renders, but
       the scroll container never overflows and "jump to latest" becomes dead
       code. */
    <div className="flex min-h-dvh lg:h-dvh">
      <Drawer
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        collapsed={collapsed && !isMobile}
      >
        <Sidebar
          conversations={conversations}
          activeId={routeConversationId}
          loading={listLoading}
          onSelect={(id) => {
            setSidebarOpen(false)
            void navigate(ROUTES.conversation(id))
          }}
          onNew={startNew}
          onDelete={handleDelete}
          onRename={(id, title) => void rename(id, title)}
          view={view}
          onView={(next) => {
            setSidebarOpen(false)
            void navigate(next === 'chat' ? chatPath() : pathForView(next))
          }}
          overdueCount={overdueCount}
          repName={repName}
          repCode={repCode}
          onLogout={() => void onLogout()}
          // ANDed with the media query so the phone drawer never opens as a rail.
          collapsed={collapsed && !isMobile}
          onToggleCollapse={toggleCollapsed}
          listError={listError}
          onRetryList={() => void refresh()}
        />
      </Drawer>

      <div
        // inert while the drawer is open: without it Tab walks out of the
        // drawer into a chat the eye is treating as unavailable.
        {...(sidebarOpen ? { inert: true } : {})}
        className={cx('grid min-w-0 flex-1 grid-rows-[auto_minmax(0,1fr)_auto]', 'h-dvh')}
      >
        <Header
          repName={repName}
          repCode={repCode}
          vintage={vintage}
          onOpenSidebar={() => setSidebarOpen(true)}
        />

        {/* <main>: the landmark screen readers navigate by. `grid` so the one
            child panel inherits the constrained row height the panels' own
            min-h-0/overflow classes rely on. */}
        <main className="grid min-h-0 min-w-0">
          <Suspense
            fallback={
              <div className="flex items-center justify-center">
                <Spinner className="size-5 text-fg-subtle" />
              </div>
            }
          >
            {view === 'agenda' ? (
              <AgendaPanel
                active
                onAsk={(prompt) => handleSend(prompt, [])}
                onOpenSettings={() => void navigate(ROUTES.settings)}
              />
            ) : view === 'settings' ? (
              <SettingsPanel active />
            ) : view === 'library' ? (
              <LibraryPanel active />
            ) : routeConversationId && missingConversation === routeConversationId ? (
              <NotFoundPage kind="conversation" />
            ) : messages.length === 0 ? (
              <Welcome name={repName} onPick={(prompt) => handleSend(prompt, [])} />
            ) : (
              /* Its own boundary so one un-renderable turn cannot take the
                 shell down with it — the sidebar and composer stay usable. */
              <ErrorBoundary
                fallback={
                  <ErrorFallback
                    title="This conversation could not be displayed"
                    body="Something in this conversation failed to render. Reload to try again — nothing has been lost."
                  />
                }
              >
                <MessageList messages={messages} onDecide={handleDecide} />
              </ErrorBoundary>
            )}
          </Suspense>
        </main>

        <Composer onSend={handleSend} onStop={stop} streaming={streaming} />
      </div>
    </div>
  )
}
