// Markdown rendering for assistant text, with a caret while tokens arrive.
//
// Raw HTML is off (react-markdown's default). Model output is not trusted
// markup: it reaches the page verbatim, and enabling rehype-raw here would
// turn a prompt injection into stored XSS.

import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

interface Props {
  content: string
  streaming?: boolean
}

export function StreamingText({ content, streaming }: Props) {
  if (!content) return streaming ? <Caret /> : null

  return (
    <div className="md">
      <Markdown
        remarkPlugins={[remarkGfm]}
        components={{
          // Wide tables scroll inside their own container so the page never
          // scrolls sideways on a phone.
          table: ({ children }) => (
            <div className="table-wrap scrollbar-thin">
              <table>{children}</table>
            </div>
          ),
          a: ({ children, href }) => (
            <a href={href} target="_blank" rel="noreferrer noopener">
              {children}
            </a>
          ),
        }}
      >
        {content}
      </Markdown>
      {streaming && <Caret />}
    </div>
  )
}

/** `data-caret` is load-bearing twice over:
 *   · prose.css excludes it from `.md > :last-child`, so the final paragraph
 *     keeps its margin when the caret unmounts. Without that the answer
 *     collapsed 8px at the very end, exactly where the reader is looking.
 *   · base.css exempts it from the reduced-motion reset, so it stops blinking
 *     but stays *visible* — a missing caret loses the only signal that tokens
 *     are still coming. */
function Caret() {
  return (
    <span
      data-caret
      aria-hidden="true"
      className="animate-caret ml-0.5 inline-block h-4 w-0.5 translate-y-0.5 rounded-full bg-accent align-middle"
    />
  )
}
