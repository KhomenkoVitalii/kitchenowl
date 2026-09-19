# KitchenOwl Food Management Extension

Product direction for this fork. See the [Roadmap](ROADMAP.md) for delivery order
and the [documentation index](README.md) for the current implementation plan.
Capabilities described here are intended outcomes, not a list of shipped features.

## Vision

Extend KitchenOwl into a lightweight system that helps manage everyday food decisions:

- what food is available;
- what can be cooked from it;
- what should be bought;
- what was cooked;
- what was actually eaten;
- and, eventually, what that means nutritionally.

The goal is not to build another calorie tracker, pantry database, or recipe browser.

The goal is to make these things **work together**.

```text
Pantry
   ↓
Recipes
   ↓
What can I cook?
   ↓
Meal planning
   ↓
Missing ingredients
   ↓
Shopping
   ↓
Cooking
   ↓
Leftovers
   ↓
Meals
   ↓
Nutrition
```

Every useful action should improve the state of the rest of the system.

If food is bought, Pantry becomes more accurate.

If something is cooked, Pantry changes and prepared food becomes available.

If a meal is eaten, leftovers and nutrition change.

If Pantry changes, recipe availability changes.

If meals are planned, the system can calculate what is missing and produce a better shopping list.

This dependency between domains is the core product value.

---

# The problem

Most existing food applications solve one part of the problem.

A calorie tracker can record what was eaten.

A shopping application can maintain a grocery list.

A recipe application can answer how to cook something.

A pantry manager can track what is stored at home.

But each usually requires maintaining its own state.

That creates work without enough immediate benefit.

For example, tracking every ingredient in a pantry is difficult to justify if that information does nothing more than produce a list of ingredients already known to the user.

The same action becomes much more valuable when Pantry automatically affects:

```text
recipe availability
shopping requirements
meal planning
cooking
leftovers
nutrition
```

Tracking should therefore not exist for its own sake.

The system should make maintaining state worthwhile by continuously using that state to reduce future work.

---

# Low friction over perfect accuracy

Food is inherently difficult to track precisely.

A person usually does not know or care whether there are exactly:

```text
437 g rice
183 ml milk
```

remaining.

What they often know is:

```text
Eggs        8 pcs
Chicken     2 portions
Rice        ~half a package
Milk        LOW
Olive oil   AVAILABLE
```

That information is already useful.

The system should support exact information where it is convenient, but should never require fake precision.

Approximate quantities, qualitative states, and uncertainty are valid data.

The same principle applies to nutrition.

If a normal serving of a dish is approximately 700 kcal, recording:

```text
Chicken rice
1 serving
~700 kcal
```

is preferable to requiring every component of the meal to be weighed.

A slightly inaccurate system that is effortless enough to use continuously is more valuable than a theoretically precise system that is abandoned.

---

# AI for complex interaction, UI for speed

KitchenOwl already provides MCP support.

This makes it possible to divide interaction into two complementary modes.

## UI

The application UI should be optimized for:

- seeing current state;
- browsing;
- quick repeated actions;
- actions that are faster to tap than explain.

Examples:

```text
[Ate]
[Cooked]
[Low]
[Out]
[Add missing]
```

The UI does not need a sophisticated form for every operation supported by the backend.

---

## MCP / AI

Complex input is often easier to describe than to enter manually.

For example:

> I have six eggs, two chicken breasts, about half a pack of rice, four tomatoes, and I'm almost out of milk.

An MCP-capable assistant can turn this into structured changes.

The same applies to more complex tasks:

> I cooked four portions of the chicken rice, but didn't use vegetables and used more chicken.

or:

> Plan dinners for the next five days. Keep them simple, use what I already have first, and try to reuse the same ingredients.

KitchenOwl should provide deterministic domain operations.

The AI should interpret the user's intent and orchestrate those operations.

```text
User
   ↓
AI / MCP client
   ↓
KitchenOwl tools
   ↓
structured application state
```

Natural-language reasoning belongs primarily to the AI client rather than the KitchenOwl domain itself.

---

# KitchenOwl as the foundation

This is an extension of KitchenOwl rather than a separate food-management application.

KitchenOwl already provides useful foundations:

```text
Items
Recipes
Shopping lists
Meal planning
Households
REST API
MCP
Flutter client
```

The extension should reuse those concepts and gradually connect the missing domains around them.

The most important additions are expected to be:

```text
Pantry / Inventory
Recipe availability
Cooking and leftovers
Actual meal consumption
Nutrition
```

The implementation order belongs in the Roadmap.

This document only defines the direction in which those capabilities should work together.

---

# Pantry should represent reality, not accounting

Pantry is the foundation because many later decisions depend on knowing what food exists.

But maintaining Pantry must remain cheap.

The system should understand several levels of knowledge:

```text
12 eggs
~half a bag of rice
milk LOW
olive oil AVAILABLE
chicken OUT
```

It must also understand the difference between:

```text
OUT
```

and:

```text
UNTRACKED
```

Not knowing whether parsley exists is different from knowing that there is no parsley.

Likewise, knowing that rice exists does not necessarily mean knowing whether there is enough for a recipe.

Uncertainty should be represented explicitly rather than hidden.

---

# Recipes should become actionable

A recipe should not only be something to read.

Once Recipes and Pantry are connected, the system can answer:

> Can I cook this?

> What am I missing?

> What recipes can I make without shopping?

> Which recipes require only one additional ingredient?

This enables concise states such as:

```text
Chicken      INSUFFICIENT
Rice         UNCERTAIN
Onion        AVAILABLE
```

and recipe-level information such as:

```text
[READY_TO_COOK]
[MISSING_1]
[PANTRY_FRIENDLY]
```

These statuses and tags should make complex system state easy to understand in both UI and AI interactions.

---

# Recipes should be cheap to acquire

The usefulness of recipe-aware planning depends on having a useful recipe library.

Creating every recipe manually would introduce unnecessary friction.

Recipes should therefore be importable from existing cooking websites wherever practical.

Useful structured information includes:

```text
ingredients
quantities
servings
preparation time
difficulty
instructions
nutrition
```

The system should normalize imported recipes into KitchenOwl's own data model.

Specific websites such as Klopotenko can be important sources and test cases, but importing should remain a generic capability rather than being tied to one publisher.

Recipes may also carry useful descriptive labels:

```text
EASY
QUICK
HIGH_PROTEIN
ONE_PAN
BATCH_FRIENDLY
```

These can later improve discovery and planning.

---

# Cooking is different from eating

A recipe describes how food can be prepared.

It does not describe what currently exists.

If four portions of curry are cooked, the system should understand that a prepared batch now exists.

```text
Recipe
   ↓
Cook
   ↓
4 prepared portions
```

Raw ingredients should be consumed when the food is prepared.

Later:

```text
prepared batch
   ↓
eat one portion
   ↓
3 portions remain
```

Eating another portion must not consume the original raw ingredients again.

This distinction between **recipe, cooked food, and actual consumption** is essential for Pantry, leftovers, meal history, and nutrition to remain consistent.

---

# Reality can differ from the recipe

Stored recipes are templates.

Actual cooking and eating are not always identical to them.

For example:

```text
Recipe:
rice + chicken + vegetables

Actual batch:
rice + extra chicken
no vegetables
```

The original recipe should remain unchanged.

The system should preserve what was actually prepared.

The same principle applies to meals.

A person may eat:

```text
1 normal portion
```

or:

```text
a larger portion
without one ingredient
with something extra
```

The system should allow these differences without turning every meal into manual ingredient accounting.

---

# Nutrition should be a consequence, not a chore

Nutrition is useful, but it should mostly emerge from information the system already has.

Ideally:

```text
Recipe
   ↓
Cooked food
   ↓
Portion
   ↓
Meal
   ↓
Calories / macros
```

The user should not need to reproduce the meal manually inside a separate calorie tracker.

If the system already knows what was cooked and approximately how much was eaten, it should be able to derive an approximate nutritional result.

Approximation should be explicit where necessary.

```text
~720 kcal
~45 g protein
```

is perfectly acceptable when the underlying food amounts were approximate.

---

# Planning should use the whole system

Eventually, planning should become one of the main benefits of connecting all these domains.

Instead of asking only:

> What recipes do I like?

the system can consider:

```text
what is already in Pantry
what is about to run out
what leftovers exist
what ingredients should be used first
what recipes are easy
what meals are filling
what was eaten recently
what nutrition goals matter
what would need to be purchased
```

A request such as:

> Plan five simple dinners. Use what I have first and reuse ingredients across several meals.

can then result in:

```text
proposed meals
      ↓
ingredient requirements
      ↓
current Pantry
      ↓
missing ingredients
      ↓
Shopping List
```

This is where the connected system becomes substantially more useful than the sum of its individual features.

---

# Shared statuses and tags

The system should expose complex state through a compact vocabulary.

There are three broad categories.

## Domain state

Examples:

```text
AVAILABLE
LOW
OUT
UNTRACKED
INSUFFICIENT
UNCERTAIN
```

These communicate facts or uncertainty about the underlying domain.

---

## Derived state

Examples:

```text
READY_TO_COOK
MISSING_1
USES_LEFTOVERS
USES_EXPIRING_ITEMS
PANTRY_FRIENDLY
```

These are calculated from other information and help with discovery and planning.

---

## Descriptive labels

Examples:

```text
EASY
QUICK
HIGH_PROTEIN
ONE_PAN
BATCH_FRIENDLY
```

These describe recipes or user preferences.

Structured values should remain structured.

For example, `700 kcal` and `4 servings` are data, not tags.

The purpose of statuses and tags is to make the state of the system easy to understand and easy for both UI and MCP clients to work with.

---

# Product boundaries

The extension should avoid becoming unnecessarily complex.

It is **not** intended to be:

- warehouse software for groceries;
- a system requiring every gram to be accounted for;
- an AI system embedded into every backend operation;
- a huge Flutter interface exposing every possible backend capability;
- a replacement for KitchenOwl's existing concepts where they already work.

The preferred approach is:

```text
structured deterministic core
+
low-friction UI
+
expressive AI/MCP interaction
```

---

# Success

The product is successful if maintaining food-related state requires little enough effort that it becomes part of normal life, while the resulting information consistently reduces future work.

A good interaction should often update several useful parts of the system indirectly.

For example:

```text
"I cooked four portions of chicken curry."
```

may eventually mean:

```text
Pantry updated
prepared food recorded
recipe usage known
future availability changed
leftovers created
nutrition can later be derived
```

The user should not need to perform those updates separately.

That is the central idea:

> **Track an event once, then let the rest of the system benefit from it.**
