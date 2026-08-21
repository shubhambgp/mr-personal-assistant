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

