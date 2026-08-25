# Frontend practices — what this codebase does, and why

`frontend.md` is the short list of hard conventions. This file is the longer
one: the practices behind them, each with the real file that demonstrates it.
Every example is code that exists in `frontend/src` — nothing here is
aspirational.

---

## 1. Conditional rendering and optional chaining

### The house rule: `?.` on every array method

Every `.map()`, `.filter()`, `.reduce()`, `.forEach()`, `.some()`, `.every()`
and `.flatMap()` in `src/` is written as `?.map(…)`, `?.filter(…)` and so on:

```tsx
{messages?.map((message) => <MessageTurn … />)}
{agenda.mail?.filter((m) => ACTIONABLE.includes(m.category))}
barsRef.current?.forEach((bar, i) => { … })
```

It is applied uniformly rather than case by case, and that uniformity is the
point: a reviewer never has to decide whether *this* iteration is the one that
needed a guard, and a field that later turns optional does not need every call
site revisited.

**Why it costs nothing in type safety.** On a receiver the type says is always
present, `?.` is a no-op to the compiler — the result does **not** widen:

```ts
declare const sure: number[]
const a: number[] = sure?.filter((n) => n > 0)     // ✓ still number[]

declare const maybe: number[] | undefined
const b: number[] = maybe?.filter((n) => n > 0)    // ✗ Type 'number[] | undefined'
```

So the rule cannot quietly hide a real nullability problem: the moment a
receiver genuinely becomes nullable, TypeScript widens the result and points at
every place that now has to handle it. The guard is free where it is
unnecessary and enforced where it is not.

### The two places `?.` must NOT go

These are not style preferences — the first is a lint error and the second is a
runtime crash.

**1. Feeding a destructure.** `String#split` always returns an array, so the
chain never short-circuits — but if it *could*, destructuring `undefined`
throws `TypeError`. ESLint's `no-unsafe-optional-chaining` refuses it:

```ts
const [hh, mm] = time.split(':')?.map(Number)   // ✗ lint error
const [hh, mm] = time.split(':').map(Number)    // ✓ and the next line validates
```
— `lib/format.ts` is the only file exempt from the house rule, for exactly this
reason. Both of its `split(...).map(...)` calls feed a destructure.

**2. Doubling up with `??`.** `images??.map(…)` is a syntax error, not a safer
version of `images?.map(…)`. If a value already has `?? []` downstream, it
already has its `?.`:

```tsx
...(images?.map((f) => ({ … })) ?? [])          // ✓ chain, then default
```

### Where `?.` is genuinely load-bearing (not just house style)

**A ref** — null before mount and after unmount:
```tsx
inputRef.current?.select()
returnFocusRef.current?.focus()
```

**A DOM query** — `querySelector` and `closest` return `null`:
```tsx
const scroller = e.currentTarget.closest('nav')
const bounds = scroller?.getBoundingClientRect()
```
— `ConversationRow.tsx`. The chain is split so the `null` case is visible at the
call site rather than buried mid-expression.

**An optional prop, field, or callback:**
```tsx
onUploaded?.()                              // onUploaded?: () => void
{!!message.attachments?.length && (…)}      // attachments?: Attachment[]
```

**Indexed access**, because `noUncheckedIndexedAccess` makes every index
`T | undefined` and the compiler demands it:
```tsx
items()[0]?.focus()
const file = e.target.files?.[0]
```

### The rest of conditional rendering

**Never render a bare number or a possibly-`0` value.** `{count && <Badge/>}`
prints `0` when the count is zero. Every numeric guard here is explicit:

```tsx
{typeof count === 'number' && count > 0 && <span>· {count}</span>}
{!!message.attachments?.length && (…)}
{typeof overdueCount === 'number' && overdueCount > 0 && <Badge/>}
```
— `AgendaPanel.tsx`, `MessageTurn.tsx`, `Sidebar.tsx`. The last one has a prop
comment explaining why `0` and "not loaded yet" must not render the same thing:
otherwise an empty badge flashes on every load.

**Watch what `?.` does to a comparison.** An optional chain that short-circuits
yields `undefined`, and `undefined === 0` is `false`, not `true`:

```tsx
{agenda.mail?.filter(isActionable).length === 0 && <p>Nothing needs a reply.</p>}
```
This is correct here only because the whole block already sits inside
`agenda && agenda.mail.length > 0 &&`. When such a comparison is the *only*
guard, compare against the value you mean or use `?? 0`.

**`??` for defaults, not `||`.** `||` also swallows `0` and `''`:

```tsx
const meta = CATEGORY[item.category] ?? FYI
{conversation.title ?? 'Untitled'}
```
And when the fallback must survive `noUncheckedIndexedAccess`, it is a **named
constant**, not another lookup: `FYI` exists precisely because `CATEGORY.fyi` is
itself possibly-undefined.

**Async UI has four states, not two.** loading / error / empty / data — and
`SettingsPanel.tsx` shows the trap worth remembering:

```tsx
{!connection ? (
  error ? null : <Spinner />
) : (
  …
)}
```
An eternal spinner *under* an error banner promises progress that is never
coming, so the spinner is suppressed once an error exists.

**"Could not load" is not "nothing here".** `useConversations` tracks a real
error flag because an offline rep used to be told "No conversations yet. Ask
something to start one." — an assertion the app could not back.

---

## 2. TypeScript

* `strict` **plus** `noUnusedLocals`, `noUnusedParameters`,
  `noFallthroughCasesInSwitch`, `noUncheckedIndexedAccess`. The last one is the
  one people disable; keep it — it is why every indexed lookup here has a
  visible fallback.
* **No `any`, no `as any`, no `@ts-ignore`, no `@ts-expect-error`.** Zero in
  `src/`. When the DOM lib lacks a type, write the structural type:
  `useVoiceInput.ts` declares 29 lines of `SpeechRecognition*` structural types
  rather than reaching for `any`.
* **Narrow `unknown`, never trust it.** `response.json()` is `any`; `api.ts`
  assigns it to `unknown` and narrows before use. `sources.ts` does the same to
  tool output.
* **Make a contract break a compile error.** The SSE union ends with:
  ```ts
  default: { const unhandled: never = event; void unhandled; break }
  ```
  Add an event type in the backend, forget the frontend, and the build fails —
  which is the whole point, because the earlier version type-checked cleanly and
  silently did nothing.
* **One definition, one place.** `View` lives in `lib/routes.ts`; every model
  lives in `lib/types.ts`. A union spelled out in three files is three files to
  find when a fifth pane arrives.

---

## 3. Component design

* **Split on responsibility, never on line count.** `AgendaPanel.tsx` was split
  because it held two features (mail/calendar view *and* a task manager), not
  because it was long — it is now 362 lines of panel plus 375 in `tasks.tsx`.
  `App.tsx` stays at ~410 lines because it is the composition root and every
  line is wiring; splitting it would produce prop-plumbing files with no
  independent meaning.
* **Extract a component when it is reusable, independently testable, or makes
  the parent readable.** Not when it is three lines used once — that trade only
  adds prop plumbing.
* **Share the variant, don't duplicate it.** `Sidebar`'s rail and full layouts
  both render the same `SettingsMenu` "so the two can never drift apart".
* **`memo()` only with a reason.** There is exactly one in the app,
  `MessageTurn`, with a comment explaining that each token publishes a new
  messages array and without it every completed turn re-renders per token. Do
  not add a second without a measurement.

---

## 4. Hooks and effects

* **Every async effect carries a cancellation flag.** `cancelled` / `alive`,
  checked **after** the `await`:
  ```ts
  if (!isCancelled()) setOverdue(list.counts.overdue)
  ```
  — `useOverdueCount.ts`, whose comment records the bug: the guard used to run
  *before* the await, so a late response still wrote state for an unmounted
  consumer.
* **Every listener and subscription is removed.** `Menu`, `Drawer`,
  `MessageList`, `ThemeProvider`, `AuthProvider` — all return a cleanup.
* **Never set state in an effect body for something derivable.** `loading` is
  computed, not tracked:
  ```ts
  const loading = documents === null && error === null
  ```
  — `useLibrary.ts`, `useAgenda.ts`. React's `set-state-in-effect` rule is
  treated as correct, not worked around: the mobile→desktop drawer close moved
  the `setState` into the `matchMedia` change callback instead.
* **Fetch only what is visible.** Panel hooks take `active` and do nothing until
  the panel is open — `useAgenda`, `useLibrary`. The agenda costs Gmail round
  trips; the chat view must not pay for them.
* **A hook lives in its own `.ts` file.** A module exporting both a component
  and a non-component breaks react-refresh. Same reason `extractSources` moved
  from `SourceList.tsx` into `sources.ts`.

---

## 5. Data fetching and state

* **Server state is fetched, never mirrored.** After a mutation the hook
  re-fetches rather than patching a local copy: "the server assigns the section,
  and a locally-inserted row would have to guess it — which is the one thing the
  browser is not allowed to decide" (`useAgenda.addTask`).
* **Optimistic only where the truth is cheap to restore.** Ticking a task
  removes the row immediately and reloads on failure. Adding a task does not —
  the section is the server's answer.
* **A failed mutation must not eat the user's input.** `addTask` re-throws after
  showing the banner so the caller keeps every typed value for the retry.
* **Keep state as local as possible.** Only auth and theme are contexts
  (`features/auth/authContext.ts`, `lib/theme.ts`). The 17 props `App` passes to
  `Sidebar` are one level deep and all used — a context there would hide the
  dependency, not remove it.
* **Filters go to the server.** `status: 'all'` is unbounded, so filtering a
  truncated list in the browser would report "nothing" when it means "nothing in
  the first page".
* **One request shape per need.** `api.tasks(filters, limit)` exists so the
  sidebar badge can ask for `limit=1` — the counts are computed server-side over
  the whole set, so the rows are pure waste.

---

## 6. Errors and failure

* **One error class, one place.** `ApiError` carries `status` and `retryAfter`;
  `request()` narrows the error body and dispatches `AUTH_EXPIRED_EVENT` on a
  mid-session 401 so the app returns to login from exactly one place.
* **Branch on the status, don't collapse it.** `LoginPage` distinguishes 429,
  401 and everything-else — because telling an offline rep "Invalid credentials"
  sends them to reset a password that was never wrong.
* **Internal strings never reach the user.** `ErrorBoundary` logs the error and
  shows a written message. Same rule as the backend's `_explain`.
* **An error boundary exists, and is layered.** One around `<App/>`, one around
  `MessageList` — so one un-renderable turn cannot take the shell down with it.
  The fallback deliberately imports **no** `ui/` primitive: if the crash came
  from a shared primitive, importing it would crash the fallback too.
* **404 means something specific.** An unknown URL and a dead conversation link
  get different sentences, because they are different mistakes.

---

## 7. Accessibility

* **Native semantics first, ARIA only to fill a gap.** Buttons are `<button>`,
  the fixed approval fields are a `<dl>`, the conversation list is a `<nav>`
  with `<ul>`.
* **An icon-only control cannot ship without a label.** `IconButton` makes
  `label` a required prop — the icon is `aria-hidden`, so without it the control
  is invisible to a screen reader.
* **Announce what changes without a click.** `aria-live="polite"` +
  `aria-atomic="false"` on streamed text; an `sr-only` `role="status"` when the
  approval card appears, because the stream just stopping is not an
  announcement.
* **If you claim a role, implement its keyboard pattern.** `Menu` says
  `role="menu"`, so it does focus-in on open, Arrow/Home/End navigation, and
  focus-return on close.
* **Hover-only is unreachable.** Every hover-revealed control also has
  `group-focus-within:` — the conversation menu was literally keyboard-invisible
  before that.
* **Trap focus in a modal surface, and restore it.** `Drawer` traps Tab, locks
  body scroll, and returns focus on close; the main column gets `inert`.

---

## 8. Styling

* **Tokens only, from `styles/theme.css`.** Tailwind's default palette is
  cleared (`--color-*: initial`), so a legacy class like `slate-500` is an
  *unknown class* that `npm run lint` fails on. That failure is the safety net:
  Tailwind emits nothing for a class it does not recognise and never warns,
  which is how `brand-950` survived four references and a green CI run.
* **No `dark:` variants.** Zero in the app, by design. `--overlay` is modelled
  as ink that inverts, so one `hover:bg-overlay/6` is correct in both themes;
  semantic colours at `/12` replace every `bg-*-100 dark:bg-*-950` pair. Needing
  `dark:` usually means the token is modelled wrong.
* **`cx` is `twMerge(clsx(...))`, not concatenation.** Argument order decides
  which class wins; plain concatenation leaves it to stylesheet order, which
  changes whenever the theme does.
* **`min-w-0` on a truncating grid/flex child is load-bearing and invisible.**
  Grid and flex items carry `min-width: auto` and refuse to shrink below their
  content. One 8-column tool result pushed the whole page into a 300px
  horizontal scroll on a phone. A "tidy the classNames" pass that removes one
  breaks nothing until content overflows.
* **Wide content scrolls inside its own container**, never the page.

---

## 9. Security (frontend's actual share)

* **The token is an httpOnly cookie.** JS cannot read it, so "am I signed in?"
  is answered by asking the server (`/api/auth/me`), never by inspecting local
  state.
* **Every API path is relative.** `/api/...` — Vite proxies in dev, nginx in
  prod. There is deliberately **no** `VITE_API_URL`: a different origin means
  the httpOnly cookie stops being sent, and a `VITE_*` value is baked into the
  public bundle anyway, so no secret could ever live there. The one thing that
  could legitimately live there is a value that is *meant* to be public — a
  Sentry DSN is the example, and `docs/SENTRY_SETUP.md` says so where it
  belongs, next to the decision.
* **Raw HTML stays off.** `react-markdown` renders model output with raw HTML
  disabled; enabling `rehype-raw` would turn a prompt injection into stored XSS.
  There is no `dangerouslySetInnerHTML` and no `eval` in `src/`.
* **`target="_blank"` always with `rel="noreferrer noopener"`.**
* **`localStorage` holds preferences only** (theme, sidebar), every access in
  `try/catch` because storage itself throws in private mode.
* **A client-side check is UX, not enforcement.** The file-count cap and the
  disabled Approve button are mirrored server-side; the comments say so. Never
  describe a frontend guard as the control.

---

## 10. Testing

* **Test the pure core.** The SSE reducer, the tool-output parser and the date
  helpers are plain functions; they carry the risk and cost nothing to test.
  `applyEvent` matters most — the backend's evals bypass HTTP entirely, so this
  is the only guarded copy of the frontend's half of the streaming contract.
* **Test through the real code, not a copy of it.** The reducer tests call the
  actual exported `patchMessage`, so the clone semantics under test are the ones
  production uses.
* **Test the mechanism a comment claims.** `tool_end` must *replace* the array
  element, not mutate it — the test asserts the previous object is untouched,
  because the failure mode (a row frozen on "running") is invisible otherwise.
* **Component tests for consequential UI only.** `ApprovalCard` gets one:
  blocked verdict cannot be approved, edits travel with the approval, discard
  sends none, and the recipient is never an input.
* **Do not chase coverage.** Nothing else in the app has a test, on purpose.

---

## 11. Tooling

* **eslint lints, Biome formats.** Biome's linter is switched off in
  `biome.json` — eslint's type-checked rules, `react-hooks` v7 and
  `better-tailwindcss/no-unknown-classes` have no Biome equivalent, and the last
  one is this project's only defence against a silently dead class.
* **Formatting is scoped to `src/**/*.ts(x)`.** CSS is excluded: `theme.css`
  carries hand-aligned contrast annotations a formatter would destroy for no
  benefit. Deliberately aligned lookup tables carry `// biome-ignore format:`.
* **The pre-commit hook is fast on purpose.** husky + lint-staged run
  `eslint --fix` and the formatter on **staged files only**; the full
  typecheck/test/build gates belong to CI. A slow hook is a hook people disable.
* **Gates, in order:** `npm run typecheck && npm run lint && npm run
  format:check && npm run test && npm run build`.

---

## 12. Comments

The house style, and the reason this codebase is navigable:

* **Explain *why*, never *what*.** `// increment the counter` is noise.
* **If a line looks wrong but is right, say why it is right.** `min-w-0`,
  `lg:z-30`, `data-caret`, the reference-equality check in `useAgenda` — each
  carries the reasoning, because each would otherwise be "tidied away".
* **Name the bug a guard exists for.** "measured at 300px before this was
  added", "the old handler checked only shiftKey", "this used to read
  'connected' over a mailbox that answered 400 to everything". A guard with a
  story survives refactoring; a bare guard does not.
* **Record what was rejected and why.** The dependency comments in
  `requirements.txt` and the "not a dependency" note in `Menu.tsx` stop the same
  decision being relitigated.
