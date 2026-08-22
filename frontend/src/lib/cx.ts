// Class merging with conflict resolution.
//
// The plain `parts.filter(Boolean).join(' ')` this replaces had a real bug:
// putting a variant class before `className` does nothing for CSS. Equal
// specificity means the winner is whichever rule Tailwind emitted *later in
// the stylesheet*, not whichever came first in the argument list. Two callers
// already overrode Button's size and worked purely by luck of that order —
// and introducing a custom text scale changes the order, so they would have
// silently reverted. twMerge resolves by argument order, which is what every
// caller already assumed.

import { clsx } from 'clsx'
import type { ClassValue } from 'clsx'
import { extendTailwindMerge } from 'tailwind-merge'

// tailwind-merge ships knowledge of the *default* theme, so it has no way to
// know our custom keys are sizes rather than colours. Left untaught, it reads
// `text-prose` as a text-colour and stops treating `text-sm text-prose` as a
// conflict — the exact failure this module exists to prevent.
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      'font-size': [{ text: ['label', '2xs', 'prose'] }],
      'rounded': [{ rounded: ['card', 'input'] }],
      'shadow': [{ shadow: ['lift', 'menu'] }],
      'animate': [{ animate: ['rise', 'slide-in', 'pop', 'scale-in', 'shimmer', 'wave', 'caret', 'rail'] }],
    },
  },
})

export const cx = (...parts: ClassValue[]) => twMerge(clsx(parts))
