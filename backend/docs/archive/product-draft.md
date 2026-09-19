# KitchenOwl Food Management Extension

> Superseded product draft. [VISION](../VISION.md) and [ROADMAP](../ROADMAP.md)
> define the current direction and phase order. In particular, cooking precedes
> consumption, and qualitative stock does not prove enough exists for a recipe.

## 1. Goal

The goal is to extend KitchenOwl from a recipe / shopping / meal-planning application into a lightweight personal food-management system.

The system should help with the full everyday flow:

```text
What do I have?
      ↓
What can I cook?
      ↓
What do I want to eat this week?
      ↓
What am I missing?
      ↓
What should I buy?
      ↓
What did I cook?
      ↓
What did I actually eat?
      ↓
How does that affect pantry and nutrition?
```

The important constraint is that the system must not become an ERP for food.

Accurate data is useful, but low-friction interaction is more important. If maintaining inventory or logging meals requires many manual actions, the system will eventually stop being used.

Therefore, approximate information is acceptable where exact information provides little practical benefit.

For example:

```text
Eggs        6 pcs
Chicken     2 portions
Rice        ~half package
Milk        LOW
Olive oil   AVAILABLE
```

is preferable to requiring the user to maintain exact weights for every item.

---

# 2. Product Principles

## Low-friction first

Common actions should require one or two interactions at most.

Examples:

```text
[Ate]
[Cooked]
[Low]
[Out]
[Add missing to shopping]
```

Complex data entry should not be implemented as large UI workflows unless necessary.

---

## AI handles complex input

KitchenOwl already exposes an MCP endpoint for tool-based interaction.

This makes MCP a first-class part of the intended workflow rather than an optional integration.

The UI should primarily handle:

- viewing state;
- browsing recipes;
- seeing today's meals;
- checking shopping items;
- performing quick actions.

Complex input can instead happen through an MCP client such as ChatGPT.

Example:

> I have six eggs, two chicken breasts, around half a pack of rice, four tomatoes and I'm almost out of milk.

The AI converts this into structured MCP calls and updates KitchenOwl.

This avoids building cumbersome forms for operations that are much easier to express using natural language.

---

# 3. Existing KitchenOwl Responsibilities

KitchenOwl already provides much of the required foundation.

Existing concepts include:

```text
Recipes
Meal planner
Shopping lists
Households
Items / ingredients
Expenses
MCP tools
```

The intention is therefore not to replace KitchenOwl or create a separate application.

Instead, the new functionality should extend the existing domain and reuse its existing concepts whenever possible.

The primary missing pieces are:

```text
Pantry / inventory
Actual food consumption
Cooked batches / leftovers
Nutrition derived from consumption
```

---

# 4. Intended Domain Flow

The central flow should eventually look approximately like this:

```text
Pantry
   ↓
Recipes
   ↓
Meal Plan
   ↓
Missing Ingredients
   ↓
Shopping List
   ↓
Shopping Completed
   ↓
Pantry Updated
   ↓
Cook Recipe
   ↓
Cooked Batch
   ↓
Eat Portion
   ↓
Meal Log
   ↓
Nutrition Log
```

Not every part needs to be implemented immediately.

The architecture should, however, avoid making later stages difficult to add.

---

# 5. Main Domain Concepts

## Pantry

Represents food currently available in the household.

A pantry item may have an exact quantity:

```text
Eggs
quantity: 8
unit: pcs
```

or an approximate quantity:

```text
Rice
quantity: 0.5
unit: package
```

or simply a state:

```text
Milk
state: LOW

Olive oil
state: AVAILABLE
```

Exact quantities should therefore not be mandatory.

---

## Recipe

A recipe is a reusable template describing a dish.

It contains:

- ingredients;
- ingredient quantities;
- number of servings;
- preparation instructions;
- optional difficulty;
- preparation time;
- nutritional information where available.

Recipes may be created manually or imported from external recipe websites.

---

## Cooked Batch

A recipe and a meal are not the same thing.

If four servings of chicken with rice are prepared, pantry ingredients should normally be consumed once during cooking rather than every time one serving is eaten.

Therefore, cooking should eventually create a `CookedBatch`.

Example:

```text
Chicken with rice

4 servings prepared
2800 kcal total
700 kcal / serving
3 servings remaining
```

This also allows leftovers to become part of the available food state.

---

## Meal / Consumption

A meal represents something actually eaten by the user.

It may reference a recipe or cooked batch but must allow modifications.

Example:

```text
Recipe:
Rice with chicken and vegetables

Meal:
1 serving

Overrides:
- no vegetables
- slightly more chicken
```

This allows nutrition calculations to represent what was actually eaten without modifying the original recipe.

---

# 6. Recipe Sources

Creating recipes manually should not be the primary workflow.

Many cooking websites already provide structured recipes containing:

- ingredients;
- quantities;
- servings;
- preparation time;
- difficulty;
- sometimes calories and macros.

The system should therefore support importing recipes from URLs.

Preferred import pipeline:

```text
URL
 ↓
schema.org Recipe / JSON-LD
 ↓
generic recipe parser
 ↓
site-specific adapter if necessary
 ↓
AI extraction fallback
 ↓
normalized KitchenOwl Recipe
```

A site such as Klopotenko is a useful initial source because recipes commonly include structured ingredient quantities, preparation complexity and nutritional information.

However, the feature should be implemented as generic `import_recipe(url)` functionality rather than as a Klopotenko-specific scraper.

---

# 7. Nutrition

Nutrition should be derived from food rather than manually entered after every meal.

The system should avoid the Yazio-style workflow of requiring exact weights for every consumed ingredient.

A recipe can calculate approximate nutrition from its ingredients:

```text
Whole recipe:
2800 kcal

Servings:
4

≈700 kcal / serving
```

When a meal is logged:

```text
Chicken with rice
1 serving
[Ate]
```

the corresponding nutritional information can be recorded automatically.

If the user modifies the meal, nutrition can be recalculated using the modified ingredient set.

Approximation is acceptable.

Consistency and ease of logging are more valuable than highly precise calorie values that require constant weighing.

---

# 8. MCP as a First-Class Interface

KitchenOwl already has MCP support.

New features should therefore expose MCP tools alongside their normal application APIs.

For Pantry, an initial tool surface could look like:

```text
get_pantry
add_pantry_item
update_pantry_item
remove_pantry_item

consume_pantry_item
restock_pantry_item

get_missing_ingredients
add_missing_ingredients_to_shopping_list
```

Future tools may include:

```text
log_meal
log_cooked_batch
get_available_meals
find_recipes_for_pantry
import_recipe
```

The MCP layer should remain deterministic and domain-oriented.

Natural-language interpretation belongs to the MCP client / LLM rather than KitchenOwl itself.

For example, KitchenOwl does not need to understand:

> I've got a little milk left.

ChatGPT can interpret this and call:

```text
update_pantry_item(
    item="milk",
    state="LOW"
)
```

This keeps the KitchenOwl API generic and reusable by other clients.

---

# 9. UI Responsibilities

The Flutter application remains important, but it should not be responsible for every possible workflow.

The UI should be optimized for quick inspection and quick actions.

Potential future screens:

```text
Today
Pantry
Recipes
Shopping
Plan
```

Example `Today` interaction:

```text
Lunch
Chicken with rice

[ Ate ]
[ Change ]
```

Example Pantry interaction:

```text
Chicken      2 portions   [-] [+]
Eggs         6            [-] [+]
Rice         ~½ pack      [Low]
Milk         LOW          [Out]
```

Bulk editing, complex planning and large inventory updates can instead be performed through MCP.

This deliberately reduces the amount of custom Flutter work required.

---

# 10. Integration Strategy

The implementation should stay close to upstream KitchenOwl architecture.

Generic capabilities should live inside KitchenOwl:

```text
models
services
REST/API controllers
MCP tools
```

AI-specific orchestration should not be embedded in the core domain.

For example:

```text
ChatGPT
    ↓
KitchenOwl MCP
    ↓
Inventory API
    ↓
Pantry domain
```

rather than:

```text
KitchenOwl
    ↓
custom ChatGPT-specific parsing logic
```

This separation keeps the features suitable for upstream contribution.

---

# 11. Upstream Contribution Strategy

The fork should remain structured so generic improvements can later be proposed to KitchenOwl upstream.

Changes should preferably be separated into relatively independent features.

For example:

```text
feature/pantry-model
feature/pantry-api
feature/pantry-mcp
feature/missing-ingredients

feature/meal-log
feature/cooked-batches
feature/nutrition
```

The Pantry implementation should align with the existing KitchenOwl inventory proposal rather than creating another competing model.

A generic Pantry backend with MCP/API support is likely much more suitable for upstream contribution than highly opinionated personal UX or AI workflows.

Our custom product direction can then remain layered on top.

---

# 12. First Implementation Step — Pantry

The first concrete implementation should be Pantry / Inventory.

It unlocks several later features simultaneously:

```text
Pantry
  ├── What can I cook?
  ├── Missing ingredient calculation
  ├── Shopping-list generation
  ├── Low-stock detection
  ├── Recipe availability
  └── AI voice inventory updates
```

The Pantry model should reuse existing KitchenOwl items rather than introduce a second independent ingredient catalogue.

Conceptually:

```text
InventoryItem
    household
    item
    storage
    quantity?
    unit?
    state?
    low_stock_threshold?
    expiry?
```

Quantity must remain optional.

This allows both:

```text
Eggs: 8 pcs
```

and:

```text
Salt: AVAILABLE
```

without forcing fake precision.

Storage locations can initially support:

```text
Pantry
Fridge
Freezer
Other
```

but they should not become a blocker for the first version.

---

## Pantry Operations

The domain should expose semantic operations instead of relying only on generic CRUD.

For example:

```text
add
set_quantity
increase
decrease
consume
restock
mark_low
mark_out
```

This will make MCP integration considerably cleaner.

Instead of an LLM having to understand internal database semantics, it can call:

```text
consume_pantry_item(...)
```

and let the domain layer perform the correct update.

---

## Pantry + Shopping

Completing shopping items should eventually allow those items to be added to Pantry.

Example:

```text
Shopping:
[x] Eggs × 10
[x] Chicken × 1 kg
```

can become:

```text
Pantry:
Eggs +10
Chicken +1 kg
```

This may be configurable because not every purchased shopping-list item necessarily needs inventory tracking.

---

## Pantry + Recipes

Recipes should be comparable against pantry contents.

Given:

```text
Recipe requires:
Chicken 400 g
Rice 250 g
Onion 1
```

and:

```text
Pantry:
Chicken 200 g
Rice AVAILABLE
Onion 2
```

the system should be able to derive:

```text
Missing:
Chicken ~200 g
```

The result can then be added directly to the existing KitchenOwl shopping list.

Approximate pantry states must be handled gracefully.

If `Rice = AVAILABLE`, the system may consider it available without attempting exact subtraction.

---

## Pantry + MCP

Pantry should ship with MCP support as part of the same feature rather than as a later integration.

This gives an immediately useful interaction even before a complete Flutter Pantry UI exists.

Example:

> Update my kitchen. I've got twelve eggs now, one pack of pasta, half a bag of rice and no chicken left.

The MCP client can translate this into the relevant Pantry operations.

This is especially important because it allows Pantry to become useful before investing heavily into custom UI.

---

# 13. Subsequent Steps

After Pantry is stable, the likely implementation sequence is:

```text
1. Pantry / Inventory
2. Missing ingredients
3. Recipe importing
4. Meal logging
5. Cooked batches / leftovers
6. Nutrition calculations
7. Meal planning improvements
8. Minimal optimized Flutter workflows
```

MCP support should evolve alongside each backend feature instead of being added at the end.

---

# 14. Long-Term Product Direction

The intended end result is not primarily a calorie tracker, pantry manager or recipe application.

It is a lightweight food-management layer where KitchenOwl stores structured state and provides fast UI interactions, while AI can operate the system through MCP.

The intended division of responsibility is:

```text
KitchenOwl
    structured state
    recipes
    pantry
    shopping
    planning
    meals
    nutrition
    quick UI actions

AI / MCP Client
    natural-language input
    bulk operations
    planning
    recommendations
    orchestration
```

The core design rule remains:

> If an action is frequent, make it one-click in the UI.

> If an action is complex, let the user describe the desired result and let the AI perform the structured operations through MCP.
