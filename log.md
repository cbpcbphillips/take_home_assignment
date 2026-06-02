# Reznar Project Development Log

This log records the main engineering checkpoints for the Reznar PDF extraction and rarity analysis project. It is a development journal, not a commit log; no commit hashes or timestamps are claimed here.

## Checkpoint 1: Problem framing

The source PDF did not expose clean selectable text, so normal PDF text extraction was not reliable. The project therefore moved to an image-first pipeline:

PDF page rendering -> vision model extraction -> raw JSON cache -> item assembly -> ontology mapping -> Pydantic validation -> Postgres loading -> rarity analysis notebook.

The goal was not only to extract text. The goal was to turn messy catalog pages into structured, validated data that could support downstream analysis.

## Checkpoint 2: Environment and database setup

The project uses `uv` for dependency management. `db.py` provides an embedded local Postgres database under `data/.pg/`, which keeps the project self-contained without requiring a separate database service.

`web.py` can launch `pgweb` for database inspection. Generated artifacts under `data/` are reproducible and ignored by Git; they should not be committed.

## Checkpoint 3: PDF rendering and image extraction

Pages were rendered as PNGs because the PDF text was not directly readable. The full run used 150 DPI because it balanced page readability, API cost, and request size.

The vision model extracted page-level item JSON. Raw extraction outputs were cached under `data/raw_extractions/` so successful API calls did not need to be paid for again.

## Checkpoint 4: API resilience issues

Running all pages at once exposed reliability issues. Some page calls failed with API connection errors, and a full restart would have wasted successful work and API credits.

The pipeline was improved so successful pages are saved immediately, page failures are recorded, and failed or missing pages can be retried without reprocessing every page. This made full extraction resumable and safer for real API usage.

## Checkpoint 5: Multi-page item issue

The initial assembly produced 86 item records, but some multi-page items were split incorrectly into separate fake records. For example, Exo-Armor was split across pages 7, 8, and 9, with continuation text treated like separate items.

A deterministic repair pass was added after initial assembly. The final repaired assembly produced 80 canonical items.

Repaired multi-page examples include:

- Exo-Armor: pages 7-9
- Ring of Elven Lords: pages 22-24
- War Drum of the Horde: pages 29-31
- Amulet of Encasement: pages 38-39

This mattered because the analysis notebook depends on one database row representing one real catalog item.

## Checkpoint 6: Initial ontology mapping issue

The deterministic mapper produced valid `MagicItem` rows, but `item_entities` stayed empty. That meant the first dataset was valid but not complete enough according to the ontology.

The missing related entities included `ItemEffect`, `ItemUsageLimit`, and `ItemChargePool`. This mattered because the rarity notebook needed structured features beyond item name, rarity, and raw text.

## Checkpoint 7: Real mapper debugging

The first real mapper calls failed because OpenAI JSON object mode required the word "json" to appear in the request instructions.

After fixing that, the mapper ran but produced many invalid entities because it tried to generate too much of the deep ontology structure at once. The final mapper strategy became layered:

- deterministic mapping creates the stable `MagicItem` parent record
- the real mapper extracts conservative related entities only
- Pydantic validation remains the quality gate

This preserved valid `MagicItem` records while still populating related ontology entities.

## Checkpoint 8: Final validated dataset

The final canonical dataset contains:

- 80 `MagicItem` records
- 269 related `item_entities`
- 231 `ItemEffect` records
- 30 `ItemUsageLimit` records
- 8 `ItemChargePool` records
- 0 validation errors

The final database tables used for analysis are `magic_items` and `item_entities`.

## Checkpoint 9: Analysis notebook

`analysis.ipynb` predicts rarity using structured fields extracted from the ontology-backed database. It reads from Postgres through `db.connect()` and does not rerun extraction, call OpenAI, or modify the database.

Features include:

- item category
- attunement
- raw text length
- effect counts
- usage limit counts
- charge pool counts
- effect category counts

The notebook compares a rarity prediction model against a baseline and is framed as exploratory because the dataset is small.

The notebook also adds an Estimated Strength Score to make the model output easier for Reznar to interpret. This is not an official pricing or balance score; it is a model-estimated power signal based on this catalog. The notebook identifies review candidates where the stated rarity and model-estimated rarity or strength do not align.

## Checkpoint 10: Final rarity review candidates

The main review candidates are:

- Horn of Bronze Dragon Control: listed very rare but predicted closer to uncommon. It may be powerful in a narrow or situational context because it targets bronze dragons specifically.
- Compass of True North: listed rare but predicted closer to legendary. Broad utility features may make it look stronger than typical rare items in the catalog.
- Amulet of Fiendish Protection: listed very rare but predicted closer to rare. It is a strong defensive item, but its benefit is limited to fiend-related threats.

These are review candidates, not automatic corrections. Reznar should use them as prompts for manual review against the original source pages.

## Checkpoint 11: Remaining limitations

The dataset is small, so model evaluation is approximate. Rarity is partly subjective and depends on lore, design intent, campaign context, and scarcity.

The extracted data may still contain minor OCR or model interpretation issues. The rarity model learns only from this catalog, not from a complete official magic item economy.

The final system is best used as a review assistant, not an automatic pricing system.
