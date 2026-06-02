# Reznar Ontology Overview

## 1. Purpose of the ontology

The Reznar ontology turns noisy PDF extraction output into structured, auditable magic item records. The source PDF is scanned/image-heavy, so extraction can be imperfect. The ontology is designed to preserve the original text while also capturing normalized fields that can be validated, loaded into Postgres, and analyzed.

The ontology is intentionally flexible. Magic item descriptions vary widely: some are short consumables, some are artifacts with pages of lore, some have charges, usage limits, drawbacks, or conditional rarity tables. The model supports that variety without requiring every item to have every possible field.

## 2. Core entity: MagicItem

`MagicItem` is the parent and canonical record. Each loaded item should have one `MagicItem` row in the database.

Important fields include:

- `name`: the item name.
- `header_line`: the original item header text when available.
- `item_category`: normalized category such as `wondrous item`, `weapon`, `armor`, `ring`, `potion`, `scroll`, `wand`, `staff`, `rod`, or `other`.
- `item_form`: physical form such as `pouch`, `helm`, `ring`, `armor`, `drum`, or `other`.
- `subtype`: extra header detail, often from parenthetical text such as `plate`.
- `rarity`: normalized rarity such as `common`, `uncommon`, `rare`, `very rare`, `legendary`, `artifact`, `varies`, or `unknown`.
- `wear_slot`: normalized equipment slot when relevant.
- `attunement`: whether attunement is required, plus any raw attunement text or requirements.
- `is_artifact`, `is_cursed`, `is_consumable`: boolean flags.
- `tags`: lightweight labels.
- `text`: structured text storage, including `raw`, `rules`, and `lore`.
- `source`: PDF source tracking.
- `extraction`: review status and warnings.

The deterministic mapper always produces a valid `MagicItem` when enough assembled text is available. The real ontology mapper layers related entities on top of this parent rather than replacing it.

## 3. Related entities

Related entities describe mechanics or metadata that belong to a `MagicItem` but are useful to analyze separately. In the database they are loaded into `item_entities` and linked back to the parent magic item.

### ItemEffect

`ItemEffect` represents one mechanical property, ability, spell option, passive feature, or triggered effect. Examples include AC bonuses, resistances, immunities, movement changes, spell casting, transformation, storage, sensing, social influence, luck effects, healing, or environmental effects.

The ontology supports both simple descriptions and richer nested fields such as activation, duration, usage limit, modifiers, damage interactions, condition effects, spells, targeting, source, and extraction metadata.

### ItemUsageLimit

`ItemUsageLimit` captures limits on how often something can be used. Examples include once per day, three times before a rest, total uses, recharge timing, or activation restrictions.

This is separate from `ItemEffect` so usage limits can be counted and analyzed even when the underlying effect text is complex.

### ItemChargePool

`ItemChargePool` represents an explicit pool of charges. It records the maximum charges, recharge details, and optional spend descriptions when the source text is clear.

The mapper is conservative here: if a maximum charge count is not explicit, a charge pool should not be created.

### ItemDrawback

`ItemDrawback` represents negative tradeoffs or risks. Examples include curses, self-damage, ability penalties, vulnerabilities, behavioral requirements, resource costs, destruction conditions, and attunement risks.

This lets harmful or risky item properties be queried separately from beneficial effects.

### ItemLore

`ItemLore` preserves story, history, origin, named figures, factions, and destruction-condition text. This is especially useful for artifacts and multi-page items where a large amount of the source text is narrative rather than rules.

### ItemRarityVariant

`ItemRarityVariant` captures conditional rarity when an item has variants by option, material, coin type, table entry, or other condition. It is used only when the source text clearly describes a rarity variation.

## 4. Why source tracking matters

Every major ontology object can include `source.pages`. This links the extracted record back to the original PDF page numbers.

Source tracking matters because:

- It makes extraction results auditable.
- It helps reviewers find the source text quickly.
- It identifies multi-page items, such as artifacts whose descriptions span several pages.
- It supports data quality checks when an item or related entity looks suspicious.

## 5. Why raw text is preserved

The ontology preserves original model-extracted text in `MagicItem.text.raw` and, where useful, in related entity descriptions or lore fields.

Raw text is important because normalized fields can lose nuance. Keeping the original text allows later review, remapping, prompt improvements, and manual correction without returning to the scanned PDF for every question.

## 6. How validation works with Pydantic

`reznar/ontology.py` defines Pydantic models and enum normalizers for all ontology entities. Module 8 validates mapped dictionaries against those models.

Validation separates successful entities from errors:

- Valid entities can be loaded into canonical database tables.
- Invalid entities are preserved as validation error records instead of being silently discarded.

This is important for AI-assisted mapping. The mapper can propose related entities, but Pydantic validation is the gatekeeper that decides whether each entity matches the ontology. `needs_review` and `warnings` mark uncertain or repaired extraction cases for follow-up.

## 7. Tradeoffs and limitations

The ontology favors auditability and flexibility over a rigid rules engine. That is a practical fit for varied magic item prose, but it has tradeoffs:

- Some mechanics remain as descriptive text instead of deeply normalized fields.
- The real mapper is conservative, so it may omit uncertain related entities.
- Validation can preserve errors, but it does not automatically fix bad source text.
- Source page links identify where text came from, not exact bounding boxes or line positions.
- Counts may change as extraction prompts and mapping prompts improve.

For the final data science notebook, the most useful fields are expected to be rarity, category, attunement, raw text features, multi-page source information, and related entity counts.
