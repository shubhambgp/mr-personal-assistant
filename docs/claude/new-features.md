# Building a new feature — ask first, plan, verify the plan, then implement

This process is mandatory for any NEW feature (a new user-facing capability, a
new tool, a new page, a new integration). It does not apply to bug fixes or
mechanical refactors — those follow `development.md` directly.

The rule in one line: **do not assume — ask.** Requirements live in the user's
head, not in the codebase, and an assumption baked into working code is far more
expensive to remove than a question was to ask.

## Step 1 — Ask before designing

Before writing any plan, put the genuine open questions to the user
(`AskUserQuestion` when available, plain questions otherwise). Ask about
decisions that change the shape of the work — not about things the codebase or
the conventions already answer. Questions that have repeatedly mattered here:

* **Scope:** what exactly should it do — and, as important, what is it NOT
  doing in v1? (The Library page shipped without download/delete because the
  user was asked and said no; assuming either would have been wasted work and
  new attack surface.)
* **Data:** does anything need to be stored? Where does it live and for how
  long? Anything sensitive goes in its own schema, never `app` — but "is this
  sensitive?" is often a product question, so ask it.
* **Who sees it:** per-rep, global, or both? This decides the tenancy story
  before any code exists.
* **UX decisions with more than one defensible answer:** placement, empty
  states, what happens on failure. Offer 2–4 concrete options with trade-offs
  rather than an open question.
* **Model-facing or human-facing?** A capability the model calls is a tool
  with an invariant checklist; a capability the human clicks is a feature
  folder. Some features are both.

If the user has already decided something, do not re-ask it. If a question is
routine judgment (naming, file layout, matching existing patterns), decide it
yourself — asking those wastes the user's attention that the real questions
need.

## Step 2 — Write the plan

A plan the user can veto in one read: short, concrete, and honest about cost.
It must name:

1. **Files touched** — created and modified, by path.
2. **The invariant checklist** — go through `security-invariants.md` and state
   which invariants the feature goes near and how each one is preserved:
   * new tool? → closes over `RepContext`, no identity/mailbox parameter,
     errors as `json.dumps({"error": ...})`, registered in `registry.py`
   * writes to Google? → built with `_write_tool()`, added to
     `GATED_TOOL_NAMES`, both directions of the gating test updated
   * new storage? → which schema, and confirm `qorvexa_ro` gets no grant
   * touches retrieval? → the only filter stays `_scope_filter()`; a
     model-supplied narrowing may steer ranking, never empty the result set
   * new untrusted text reaching the model? → the "data, not instructions"
     rule goes in the tool description, not the payload
3. **Contract changes** — new SSE events, new endpoints, new env variables
   (and which template each lands in).
4. **The verification plan** — which gates, which new tests (mechanism-level
   for anything security-shaped), and what the end-to-end check through the
   real transport will be.
5. **What is deliberately out of scope**, so cut corners are visible choices,
   not surprises found later.

## Step 3 — Verify the plan with the user

Show the plan and wait for confirmation **before implementing**. This is the
cheapest moment to change direction. If the user amends it, update the plan —
do not silently deviate from what was agreed during implementation. If
implementation later reveals the plan was wrong somewhere, say so and ask,
rather than quietly building something different.

## Step 4 — Implement

Follow `development.md`'s loop and the recipes in `skills.md` (add a column,
add a read-only tool, add a write-capable agenda tool, add a frontend surface —
these exist so a new feature starts from the proven path, not from scratch).
Finish means finished: gates green, end-to-end check done through the real
transport, temporary artefacts cleaned up, docs and env templates updated in
the same change.

## Why this process exists

Two failures it prevents, both of which happened in this repo's history:

* **Building the wrong thing well.** A feature that satisfies every invariant
  and every gate but not the user is pure waste — and the only defence is the
  questions in step 1.
* **Breaking a security property while adding something harmless-looking.**
  Every invariant in `security-invariants.md` is one line of code away from
  silently gone; the checklist in step 2 forces the collision check while the
  feature is still on paper.
