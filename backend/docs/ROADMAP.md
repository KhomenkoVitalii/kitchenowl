# KitchenOwl Food Management Extension — Roadmap

Status: Phase 1 design documented; implementation has not started.
Direction: [Vision](VISION.md). Start development with the
[Pantry implementation checklist](implementation/pantry.md); use the
[Phase 1 specification](design/pantry.md) for behavior and contracts.

## Delivery at a glance

| Stage | Usable outcome | Gate before moving on |
| --- | --- | --- |
| **Now: Phase 1 — Pantry + MCP** | Record a household inventory, consume, restock and correct it through REST/MCP. | Complete P1-01 through P1-04, including a real client and household usage trial. |
| **Next: Phase 2 — Availability** | Explain what a recipe lacks and explicitly add known shortages to shopping. | Real recipes produce useful results; unknown quantities remain visible. |
| Phase 3 — Import quality | Add usable recipes from URLs with little cleanup. | Validate a representative source set and record cleanup needed. |
| Phase 4 — Cooking | Cook once, deduct ingredients once, keep prepared portions. | Batch snapshots and stock remain consistent after overrides and retries. |
| Phase 5 — Consumption | Record eating and reduce remaining portions. | Meal history stays stable and never deducts the raw ingredients twice. |
| Phase 6 — Nutrition | Derive approximate calories/macros from recorded food. | Missing data is explicit; historical totals stay stable. |
| Phase 7 — Planning | Propose meals and generate their shopping requirements. | A household can use the proposal and resulting list for a real planning cycle. |
| Phase 8 — UI optimization | Make frequent actions faster to tap. | Build around repeated friction observed in earlier phases. |

The shared vocabulary is part of each phase's contract, not a separate framework
project. Phase 1 introduces only Pantry states. Small UI improvements can accompany
earlier phases when usage justifies them; Phase 8 is the broader optimization pass.
Only Phase 1 currently has an implementation specification. Later rows define
direction and gates; they are not fully designed work packages or date commitments.

## Purpose

This roadmap describes **the order in which the product should be built**.

It complements the higher-level product vision and intentionally avoids duplicating detailed implementation decisions.

The structure is:

```text
Vision
  ↓
Roadmap
  ↓
Phase-specific design/specification
  ↓
Implementation
```

The Vision explains **what the product should become and why**.

This Roadmap explains **how we get there incrementally**.

Each phase-specific design document explains **exactly how that phase behaves and is implemented**.

---

# Guiding delivery strategy

The system should grow through usable vertical slices.

Each phase should:

1. unlock a real user workflow;
2. be usable through existing KitchenOwl interfaces where practical;
3. expose MCP tools when they materially improve interaction;
4. be dogfooded before the next major domain is designed;
5. reveal what the next phase actually needs.

Do not attempt to fully design future domains before earlier assumptions have been validated.

```text
build
→ use
→ observe friction
→ refine domain
→ continue
```

rather than:

```text
design the complete food system
→ implement everything
→ use it for the first time
```

---

# Current foundation

KitchenOwl already provides the major surrounding domains:

```text
Items / ingredients
Recipes
Shopping lists
Meal planning
Households
Users
MCP
REST API
Flutter client
```

We should extend these existing concepts rather than build a parallel application.

The main missing domains are:

```text
Inventory / Pantry
Recipe availability
Cooking / leftovers
Actual consumption
Nutrition
```

The roadmap adds them in that order because each later capability depends on information produced by the earlier one.

---

# Foundation — Shared statuses and tags

## Goal

Introduce a small shared vocabulary that allows Pantry, recipes, planning, MCP and UI to communicate useful state without exposing all underlying calculations.

These should not be arbitrary free-form labels.

There are three distinct concepts.

---

## 1. Domain status

These represent known or inferred system state.

Initial examples:

```text
AVAILABLE
LOW
OUT
INSUFFICIENT
UNCERTAIN
UNTRACKED
```

Later domains may introduce:

```text
EXPIRED
EXPIRING_SOON
```

Their meaning must remain explicit.

For example:

```text
UNTRACKED != OUT
UNCERTAIN != INSUFFICIENT
LOW != INSUFFICIENT
```

An ingredient can therefore be represented as:

```text
Chicken    INSUFFICIENT
Rice       UNCERTAIN
Onion      AVAILABLE
Milk       LOW
Parsley    UNTRACKED
```

These statuses should be returned by domain services and APIs rather than recreated independently by Flutter or MCP.

---

## 2. Derived planning tags

These are calculated from underlying state and help users scan or filter options quickly.

Examples:

```text
READY_TO_COOK
MISSING_1
MISSING_FEW
USES_LEFTOVERS
USES_EXPIRING_ITEMS
PANTRY_FRIENDLY
```

Example:

```text
Chicken rice
[READY_TO_COOK]

Carbonara
[MISSING_1]

Vegetable soup
[USES_EXPIRING_ITEMS]
```

These tags are derived information.

They should generally not be stored as manually editable truth if they can be recomputed.

---

## 3. Recipe/user labels

These describe properties useful for search, planning and preference.

Examples:

```text
QUICK
EASY
HIGH_PROTEIN
ONE_PAN
BATCH_FRIENDLY
FREEZER_FRIENDLY
BREAKFAST
DINNER
```

Unlike domain statuses, these may originate from:

- recipe source metadata;
- user assignment;
- imported recipe data;
- later automated classification.

---

## Structured data should remain structured

Not every property should become a tag.

Avoid labels such as:

```text
700_KCAL
45G_PROTEIN
4_SERVINGS
```

These remain structured fields.

Tags describe classification or state, not values that already have a proper data representation.

---

## Why this is foundational

The same concepts can later power all interfaces:

```text
Domain state
     ↓
REST / MCP
     ↓
Planner
     ↓
Flutter badges / filters
```

Example MCP request:

> Show me EASY meals that are READY_TO_COOK.

Example planning request:

> Give me HIGH_PROTEIN recipes where I am missing at most one ingredient.

Example UI:

```text
Chicken curry
[EASY] [HIGH_PROTEIN] [READY_TO_COOK]
```

The exact vocabulary can evolve as later phases reveal new states, but the distinction between:

```text
domain status
derived planning tag
descriptive recipe label
```

should remain stable.

---

# Phase 1 — Pantry backend + MCP

## Goal

Allow KitchenOwl to know **what food currently exists in the household**.

The feature must already be useful without a dedicated Flutter Pantry screen.

The primary interaction can initially happen through MCP.

Example:

> I have twelve eggs, half a bag of rice, a little milk, and no chicken left.

After the interaction, KitchenOwl should contain structured Pantry state that can be queried and changed later.

---

## Build

Extend KitchenOwl's existing Inventory work rather than introduce a competing storage model.

Add support for:

```text
storage locations
inventory entries linked to existing Item
exact quantities
estimated quantities
qualitative stock states
```

Examples:

```text
Eggs
12 pcs
AVAILABLE
```

```text
Rice
~0.5 package
AVAILABLE
```

```text
Milk
LOW
```

```text
Chicken
OUT
```

The shared status vocabulary introduced in the foundation should be used rather than Pantry-specific ad-hoc strings.

Implement semantic operations such as:

```text
set total
consume
restock
mark available
mark low
mark out
remove from tracking
```

REST and MCP must share the same domain/service behavior.

Provide an atomic bulk operation so an MCP client can update many observed pantry items together.

---

## User-visible result

These conversations should work:

> I have 8 eggs, two chicken breasts and half a bag of rice.

> I used two eggs.

> I bought ten eggs.

> There's no chicken left.

> What do I currently have?

The result should expose meaningful states:

```text
Eggs       AVAILABLE
Chicken    OUT
Rice       AVAILABLE
Milk       LOW
```

No Flutter-specific work is required to validate the phase.

---

## Validate through dogfooding

Use Pantry through ChatGPT/MCP for real household updates.

Observe:

- whether exact vs approximate quantities are sufficient;
- whether qualitative states are useful;
- whether `AVAILABLE / LOW / OUT` semantics feel natural;
- whether `set`, `consume`, and `restock` match natural-language usage;
- how often unit ambiguity occurs;
- whether bulk updates feel natural;
- which operations are missing.

Domain semantics should be adjusted here before Pantry becomes a dependency of other features.

---

## Not part of this phase

Do not yet implement:

```text
recipe availability
missing ingredient calculations
automatic shopping transfer
meal logging
cooked batches
nutrition
full Flutter Pantry UI
AI-specific parsing inside KitchenOwl
```

MCP clients perform natural-language interpretation.

KitchenOwl remains deterministic.

---

## Exit condition

Move forward when Pantry can reliably be maintained through REST/MCP and the real voice/text workflow feels natural enough to use continuously.

---

# Phase 2 — Pantry ↔ Recipes: availability and missing ingredients

## Goal

Answer:

> What can I cook with what I have?

and:

> What do I need to buy to cook this recipe?

This is the first phase where Pantry becomes useful beyond inventory tracking itself.

---

## Build

Compare recipe ingredients against Pantry.

For every required ingredient, derive a shared domain status:

```text
AVAILABLE
INSUFFICIENT
UNCERTAIN
OUT
UNTRACKED
```

Example:

```text
Recipe:
Chicken rice

Needs:
400 g chicken
250 g rice
1 onion

Pantry:
200 g chicken
rice AVAILABLE
2 onions
```

Result:

```text
Chicken    INSUFFICIENT
Rice       UNCERTAIN
Onion      AVAILABLE
```

`UNCERTAIN` is important.

If Pantry only knows:

```text
Rice
AVAILABLE
```

the system knows that rice exists but cannot prove there is enough for a recipe requiring `250 g`.

That should not be silently converted into either `AVAILABLE` or `MISSING`.

---

## Recipe-level derived tags

Ingredient statuses can be aggregated into planning tags.

For example:

```text
all required ingredients AVAILABLE
→ READY_TO_COOK
```

```text
exactly one ingredient OUT/INSUFFICIENT
→ MISSING_1
```

```text
several ingredients missing
→ MISSING_FEW
```

Missing counts cover known shortages only. Return untracked/uncertain counts
separately: `MISSING_1` with two uncertain ingredients does not mean that buying one
item guarantees the recipe is cookable. `READY_TO_COOK` requires every required
ingredient to be demonstrably sufficient. Optional ingredients do not block it.

This enables recipe discovery without requiring the user to inspect every ingredient.

---

## Important design work

KitchenOwl recipes currently contain ingredient quantities that may not always be machine-comparable.

This phase is where we should solve only the amount parsing and unit handling that the actual Pantry/Recipe comparison requires.

Do not create a universal cooking-unit engine unless usage proves it necessary.

The system must preserve uncertainty rather than pretend unknown values are exact.

---

## MCP workflows

Examples:

> Can I cook chicken curry tonight?

> Which recipes are READY_TO_COOK?

> Show me EASY recipes where I'm only MISSING_1 ingredient.

> Add everything missing for this recipe to my shopping list.

---

## Not part of this phase

Do not automatically deduct Pantry when merely selecting or planning a recipe.

Do not introduce cooking or eating semantics yet.

---

## Exit condition

Given real recipes and real Pantry state, the system can produce useful ingredient statuses and recipe-level planning tags and generate correct Shopping List changes.

---

# Phase 3 — Recipe acquisition and normalization

## Goal

Make it easy to build a useful personal recipe library without manually entering every recipe.

---

## Build

Strengthen the existing KitchenOwl recipe import path.

The preferred pipeline is:

```text
Recipe URL
    ↓
structured recipe data / JSON-LD
    ↓
existing/generic parser
    ↓
site-specific handling when necessary
    ↓
normalized KitchenOwl Recipe
```

AI extraction may be used as a fallback where structured parsing fails, but KitchenOwl should store the final result as normal recipe data.

---

## Import useful labels

When recipe sources expose suitable information, preserve or derive useful labels such as:

```text
EASY
QUICK
BREAKFAST
DINNER
```

Other labels such as:

```text
HIGH_PROTEIN
BATCH_FRIENDLY
FREEZER_FRIENDLY
```

may later be derived from structured recipe information or explicitly assigned.

Labels should remain useful for filtering and planning rather than becoming an uncontrolled collection of strings.

---

## Initial real-world sources

Use several representative recipe sites.

Klopotenko is a useful first-class test case because it contains:

```text
ingredients
quantities
servings
difficulty
preparation information
nutrition in some recipes
```

But architecture must remain generic.

Do not build a "Klopotenko scraper" as the core feature.

---

## User-visible result

These should become normal workflows:

> Import this recipe.

> Save these five recipes from these URLs.

> Show me imported EASY recipes.

The result should become usable by Phase 2 Pantry availability immediately.

---

## Exit condition

Adding a new recipe becomes cheap enough that maintaining a personal recipe library is realistic.

A meaningful percentage of recipes from the chosen sources should require no manual cleanup.

When designing Phase 3, choose a fixed sample of URLs and a cleanup target before
implementing changes, then report results against it. This makes the exit condition
measurable without guessing source coverage in advance.

---

# Phase 4 — Cooking and leftovers

## Goal

Represent the difference between:

```text
a recipe
```

and:

```text
food that was actually cooked and now exists
```

This prevents Pantry from being consumed repeatedly each time leftovers are eaten.

---

## New domain concept

Introduce a `CookedBatch` or equivalent concept.

Example:

```text
Chicken curry

Recipe:
Chicken curry

Prepared:
4 servings

Remaining:
4 servings

[BATCH_FRIENDLY]
[LEFTOVER]
```

Cooking the batch consumes its ingredients from Pantry **once**.

Eating it later consumes servings from the batch rather than ingredients from Pantry again.

---

## Build

Support:

```text
cook recipe
record number of servings
consume recipe ingredients
create cooked batch
track remaining portions
finish/discard batch
```

Cooking must support ingredient overrides because real cooking may differ from the stored recipe.

Example:

```text
Recipe:
rice + chicken + vegetables

Today:
no vegetables
extra chicken
```

The batch should preserve what was actually prepared rather than mutate the original Recipe.

---

## Derived planning information

Cooked food may expose derived states such as:

```text
LEFTOVER
EXPIRING_SOON
```

This later allows planning to prefer food that already exists.

---

## MCP workflows

Examples:

> I cooked the chicken rice recipe, four portions, but without vegetables.

> I used all remaining chicken instead of the recipe amount.

> I threw away the last portion.

---

## Exit condition

Prepared food and raw Pantry stock can coexist correctly, and cooking a batch changes Pantry exactly once.

---

# Phase 5 — Meal / consumption logging

## Goal

Record **what the user actually ate**.

This is a separate concern from what was cooked.

---

## New domain concept

Introduce `Meal` / `Consumption`.

A consumption record can reference:

```text
a CookedBatch portion
a Recipe directly
an ad-hoc meal
```

Example:

```text
Dinner
Chicken rice
1 portion
20:10
```

or:

```text
Dinner
Chicken rice
1 portion
override:
+ extra chicken
- vegetables
```

Historical meal data should be a snapshot.

Later recipe edits must not rewrite the past.

---

## Quick interaction

This is the point where dedicated UI starts becoming especially valuable.

Typical action:

```text
Chicken rice
[ Ate ]
```

The system already knows enough to perform the rest.

More unusual modifications can continue through MCP.

---

## MCP workflows

Examples:

> I just ate one portion of yesterday's curry.

> I ate the rice with chicken but didn't add vegetables.

> What did I eat today?

---

## Exit condition

Actual consumption can be recorded with very little effort and remains historically accurate.

---

# Phase 6 — Nutrition

## Goal

Calculate useful calorie and macro information **without turning food logging into manual accounting**.

---

## Principle

Nutrition should follow from food already known to the system.

The user should not normally enter:

```text
173 g chicken
148 g rice
11 g oil
```

after eating.

Instead:

```text
Recipe
→ CookedBatch
→ portion
→ Meal
→ approximate nutrition
```

---

## Build

Associate ingredient nutrition data with recipe ingredients.

Calculate:

```text
recipe nutrition
batch total nutrition
nutrition per portion
meal nutrition
daily totals
```

Support uncertainty and incomplete nutrition data.

Prefer:

```text
~720 kcal
~45 g protein
```

over false precision.

Nutrition values remain structured data.

Derived labels such as:

```text
HIGH_PROTEIN
```

may be exposed separately when useful for filtering and planning.

---

## Overrides

If a meal or cooked batch differs from the recipe, nutrition should use that snapshot.

Historical values should remain stable even if:

```text
recipe changes
nutrition database changes
ingredient mappings change
```

---

## MCP workflows

Examples:

> Roughly how many calories did I eat today?

> How much protein did I get yesterday?

> Show me HIGH_PROTEIN meals I can cook.

> This serving was probably 30% bigger than usual.

---

## Exit condition

Nutrition becomes a useful passive output of normal food tracking rather than another workflow the user must maintain.

---

# Phase 7 — Pantry-aware planning

## Goal

Use all accumulated state to simplify weekly food planning.

By this point the system knows:

```text
recipes
recipe labels
pantry
availability statuses
shopping
cooked leftovers
meal history
nutrition
```

Planning can therefore become substantially smarter.

---

## Build

Support questions such as:

```text
What should I cook this week?
What should I use before buying more?
Which ingredients can be reused across several meals?
What can I cook in under 20 minutes?
What food should be consumed first?
```

The planner can combine statuses and tags:

```text
READY_TO_COOK
MISSING_1
USES_LEFTOVERS
USES_EXPIRING_ITEMS

EASY
QUICK
HIGH_PROTEIN
BATCH_FRIENDLY
```

Generate a proposed plan rather than silently modifying state.

Once approved:

```text
Meal plan
    ↓
required ingredients
    ↓
Pantry comparison
    ↓
Shopping List
```

---

## MCP is the main interface

This is intentionally a complex interaction and therefore does not require an equally complex Flutter planning UI.

Example:

> Plan five dinners for this week. Keep them EASY and filling. Prefer READY_TO_COOK meals and recipes that USE_EXPIRING_ITEMS. Reuse the same core ingredients where possible.

The LLM orchestrates existing deterministic KitchenOwl tools.

---

## Exit condition

Weekly planning meaningfully reduces the amount of manual decision-making and produces a useful Shopping List.

---

# Phase 8 — UI optimization

## Goal

Build UI only for interactions where MCP/voice is slower than tapping.

By now real usage should show which actions occur repeatedly.

Statuses and tags provide a compact representation suitable for mobile UI.

---

## Likely candidates

### Today

```text
Lunch
Chicken curry
[HIGH_PROTEIN]

[Ate]
[Change]
```

### Pantry glance

```text
Eggs         6        [AVAILABLE]
Chicken               [OUT]
Milk                  [LOW]
Rice         ~½       [AVAILABLE]
```

### Recipes

```text
Chicken curry
[EASY] [HIGH_PROTEIN] [READY_TO_COOK]

Carbonara
[QUICK] [MISSING_1]
```

### Leftovers

```text
Chicken curry
2 portions left
[LEFTOVER]

[Ate one]
```

---

## Rule

Do not create complex Flutter workflows merely because a backend capability exists.

A backend/MCP feature does not automatically require equivalent UI.

The UI exists for **speed and state visibility**, while MCP exists for **expressiveness**.

---

# Upstream contribution track

Personal product development and upstream contribution are related but separate processes.

The recommended sequence is:

```text
implement a useful phase
        ↓
dogfood it
        ↓
stabilize semantics
        ↓
harden integration
        ↓
prepare upstream contribution
```

Upstream readiness may require additional work such as:

```text
export/import
migration compatibility
socket parity
complete lifecycle integration
concurrency hardening
documentation
broader tests
```

Those requirements should not necessarily block initial personal validation.

For Phase 1 the boundary is explicit in the [specification](design/pantry.md):
permissions, atomicity, conditional-write protection, deletion integrity and a safe
merge guard are personal-release requirements. Full merge reconciliation, portable
exports, live events and the complete database matrix are follow-up integration.
Basic migration correctness and testing on the actual deployment database always
precede daily use. See [P1 and U tasks](implementation/pantry.md) for the gates.

Each phase-specific design document should explicitly distinguish:

```text
Personal MVP requirements

vs.

Upstream-ready requirements
```

---

# Documentation structure

The project should maintain three distinct documentation layers.

## 1. Product Vision

Answers:

```text
What are we building?
Why?
What principles guide the product?
How should KitchenOwl, UI and MCP interact?
```

This document should change rarely.

---

## 2. Roadmap — this document

Answers:

```text
What do we build first?
What comes next?
Why is the order important?
What usable capability does each phase unlock?
When is a phase complete?
```

It should contain very little low-level implementation detail.

---

## 3. Phase design documents

Example:

```text
docs/design/pantry.md
docs/design/recipe-availability.md
docs/design/cooking.md
docs/design/consumption.md
docs/design/nutrition.md
```

Paths are relative to `backend/`. The existing [Pantry design](design/pantry.md)
is accompanied by a [delivery checklist](implementation/pantry.md). The design
defines behavior; the checklist provides ordered tasks, demonstrations and evidence.
These are two views of the phase-specific layer, not separate product roadmaps.

These answer:

```text
How exactly should this phase behave?
What exists upstream already?
Which design questions remain?
What decisions were made?
Why?
What are the API/MCP contracts?
What are the edge cases?
How will it be tested?
What is required for personal MVP?
What additional work is required for upstream?
```

A phase document is allowed to be detailed.

It should evolve from questions into recorded decisions.

Once a question is answered, the answer should replace ambiguity rather than remain buried in chat history.

---

# Immediate next action

The active roadmap stage is:

```text
Foundation
    ↓
Phase 1 — Pantry backend + MCP
```

The Pantry design should therefore also establish the first shared domain statuses required by Pantry:

```text
AVAILABLE
LOW
OUT
UNTRACKED
```

Phase 2 can then extend their usage with:

```text
INSUFFICIENT
UNCERTAIN
```

and introduce the first recipe-level derived planning tags.

The [Phase 1 design/specification](design/pantry.md) now records the relevant
repository research, working decisions, REST/MCP behavior and open delivery checks.
The earlier plan is retained as [historical research](archive/pantry-plan.md).

Start **P1-01: record and read stock through REST and MCP** in the
[implementation checklist](implementation/pantry.md). Its first demonstration is:

```text
MCP: record 12 eggs in this household's Pantry
REST: read the same entry
Another household member: read the same state
Nonmember / cross-household write: rejected
```

Then implement corrections and consume/restock (P1-02), atomic bulk updates
(P1-03), and real-client deployment/usage validation (P1-04). Only after that gate
do we write the Phase 2 specification. Upstream integration tasks U1–U5 have their
own completion criteria and do not silently expand the personal release.
