// The one breakpoint the shell branches on, owned in one place. Drawer uses it
// to decide dialog semantics and inert; App uses its negation to gate the
// desktop-only sidebar collapse. Keeping a single media-query string means the
// two can never disagree about where "mobile" ends.

import { useEffect, useState } from 'react'

export function useIsMobile(): boolean {
  const [isMobile, setIsMobile] = useState(false)

  useEffect(() => {
    const mq = window.matchMedia('(max-width: 1023px)')
    const update = () => setIsMobile(mq.matches)
    update()
    mq.addEventListener('change', update)
    return () => mq.removeEventListener('change', update)
  }, [])

  return isMobile
}
