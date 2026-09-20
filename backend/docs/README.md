# Food management fork

This fork connects KitchenOwl's existing recipes, shopping and planning with
Pantry, cooking, leftovers and actual consumption. The product direction is
defined; the next work is **Phase 1: Pantry backend + MCP**.

**Current status:** P1-01 through P1-03 implemented on `phase/01-pantry` — Pantry
locations, stock create/read, correct/consume/restock/mark/remove, location edits, and
atomic bulk `apply_pantry_changes` (REST + MCP, both transports) with revision-conditional
writes, the merge guard and the migration, all with tests. P1-04 (real-deployment gate) is next.

| Read | Use it for |
| --- | --- |
| [Vision](VISION.md) | Product intent and principles. |
| [Roadmap](ROADMAP.md) | Phase order, dependencies and user outcomes. |
| [Phase 1 implementation checklist](implementation/pantry.md) | What to implement next, in order, and how to prove each step works. Start here for development. |
| [Phase 1 specification](design/pantry.md) | Pantry behavior, data, REST/MCP contracts and recorded decisions. |
| [Pantry setup & usage](pantry-usage.md) | User-facing reference: enabling, auth, routes/tools, quantity & retry rules, errors, backup limitation. |

The first release lets a household record eggs, estimated rice, LOW milk and OUT
chicken in one MCP operation, then consume, restock and correct those entries.
It requires no Flutter work. Recipe comparison starts after this workflow has
been used and its friction recorded.

The specification defines behavior; the checklist tracks delivery. Change both
when implementation changes a decision. Mark a task complete only with linked
implementation or validation evidence. Future phase specifications are written
when their prerequisites have been validated, not all at once.

Personal release and upstream contribution have separate completion criteria.
Household isolation, atomic writes and protection against repeated deductions
belong in the first release. Portable exports, live notifications and full merge
support have their own follow-up tasks.

Earlier [product](archive/product-draft.md) and [Pantry](archive/pantry-plan.md)
drafts are retained for research history. They are not implementation instructions.

## Branch workflow

```text
main                         upstream baseline
└── fork/food-management     vision, plans and completed fork features
    └── phase/01-pantry       active Phase 1 implementation
```

Keep `main` as the upstream baseline. `fork/food-management` is the long-lived
integration branch: it starts with these documents and accumulates completed
phases. Create one branch per phase from that integration branch, and commit all
tasks for the phase there. No per-task branches or stacked PRs are needed for
solo development.

For Phase 1, work through P1-01 to P1-04 on `phase/01-pantry`, keeping the spec and
checklist current alongside the code. Merge it into `fork/food-management` after
the personal release gate passes, then create the next phase branch from the
updated integration branch. Follow-up integration tasks remain separate checklist
items and do not require their own branches.

Use focused commits within each phase so changes remain easy to inspect or revert.
If upstream changes are needed during a phase, update `main`, merge it into the
fork integration branch, then merge that branch into the active phase branch.
