# Frontend conventions

Applies to everything under `frontend/`.

## TypeScript

* `strict`, no `any`. The SSE event union in `lib/types.ts` mirrors the
  backend's — add a case there and the compiler will find every place that
  needs updating.
* `npm run typecheck && npm run lint && npm run build` must all pass.

## Structure

* Feature-first: a feature is ONE folder under `features/` — components, hooks
  and its own state together. New UI surface = new `features/<name>/`, touching
  nothing else.
* `components/ui/` is shared primitives only. If it is chat-specific it does
  not belong there.
* `app/` (shell, providers, layout) owns nothing domain-ish.

## Design tokens and theming

* **Design tokens live in `styles/theme.css`. Nowhere else.** Tailwind's default
  palette is deliberately cleared (`--color-*: initial`), so there is no
  `slate-500` to reach for: a legacy colour becomes an *unknown class* that
  `npm run lint` fails on. Tailwind emits nothing for a class it does not
  recognise and never warns — that is exactly how `brand-950` survived four
  references and a green CI run.
* **Do not write `dark:` variants.** There are zero in the app and that is the
  design, not an accident. `--overlay` is modelled as *ink* that inverts with the
  theme, so one `hover:bg-overlay/6` is correct on every surface in both modes;
  and semantic colours at `/12` (`bg-danger/12`) replace every
  `bg-*-100 dark:bg-*-950` pair. If you find yourself needing `dark:`, the token
  is probably modelled wrong.

