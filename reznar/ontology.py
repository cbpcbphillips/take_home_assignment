"""Reznar's Arcane Oddities -- domain ontology.

Design goals:
- Capture the source header structure: category, subtype/form, rarity, attunement.
- Normalize equipment slots so inconsistent names like helm/mask/crown conflict correctly.
- Preserve raw rules/lore/source text so extraction remains auditable.
- Model common mechanics: charges, usage limits, saves, damage, conditions, modifiers, spells.
- Represent complex item mechanics as linked ontology entities, similar to the Stormland example.
- Allow partial/noisy extraction by keeping most fields optional and adding review metadata.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Optional
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator


# ---- Markers + validators ---------------------------------------------------
# Pattern: each field type is `Annotated[base_type, BeforeValidator, Hint]`.
#   * BeforeValidator normalizes whatever the LLM produces into a canonical form.
#   * Hint describes that canonical form so prompt-rendering can show it to
#     the LLM up front — fewer round trips through ValidationError.


class Hint:
    """Free-text description for LLM agents, carried as Annotated metadata."""

    __slots__ = ("text",)

    def __init__(self, text: str):
        self.text = text


def _to_non_empty_text(v: object) -> str:
    if v is None:
        raise ValueError("value is required")
    text = str(v).strip()
    if not text:
        raise ValueError("value cannot be empty")
    return text


def _collapse_ws(text: str) -> str:
    return " ".join(text.split())


def _normalize_key(v: object) -> str:
    text = _collapse_ws(_to_non_empty_text(v)).lower()
    return text.replace("_", " ").replace("-", " ")


def _to_label(v: object) -> str:
    return _collapse_ws(_to_non_empty_text(v))


def _to_text(v: object) -> str:
    return _to_non_empty_text(v)


# ---- Enums ------------------------------------------------------------------


class Rarity(str, Enum):
    COMMON = "common"
    UNCOMMON = "uncommon"
    RARE = "rare"
    VERY_RARE = "very rare"
    LEGENDARY = "legendary"
    ARTIFACT = "artifact"
    VARIES = "varies"
    UNKNOWN = "unknown"


class ItemCategory(str, Enum):
    """Top-level category from the item header line."""

    WONDROUS = "wondrous item"
    WEAPON = "weapon"
    ARMOR = "armor"
    RING = "ring"
    ROD = "rod"
    STAFF = "staff"
    WAND = "wand"
    POTION = "potion"
    SCROLL = "scroll"
    OTHER = "other"


class ItemForm(str, Enum):
    """Physical form/base object, usually inferred from name or subtype."""

    AMULET = "amulet"
    NECKLACE = "necklace"
    CLOAK = "cloak"
    CAPE = "cape"
    GOWN = "gown"
    BOOTS = "boots"
    GLOVES = "gloves"
    BELT = "belt"
    HELM = "helm"
    HELMET = "helmet"
    MASK = "mask"
    CROWN = "crown"
    HEADBAND = "headband"
    BRACERS = "bracers"
    GAUNTLETS = "gauntlets"
    RING = "ring"
    ARMOR = "armor"
    SHIELD = "shield"
    DAGGER = "dagger"
    SWORD = "sword"
    GREATSWORD = "greatsword"
    AXE = "axe"
    BATTLEAXE = "battleaxe"
    MACE = "mace"
    WARHAMMER = "warhammer"
    WAR_PICK = "war pick"
    RAZOR = "razor"
    STAFF = "staff"
    WAND = "wand"
    ROD = "rod"
    POTION = "potion"
    ELIXIR = "elixir"
    SCROLL = "scroll"
    CHEST = "chest"
    BACKPACK = "backpack"
    POUCH = "pouch"
    COMPASS = "compass"
    CANTEEN = "canteen"
    SHOVEL = "shovel"
    DRUM = "drum"
    HORN = "horn"
    PIPE = "pipe"
    CALTROPS = "caltrops"
    INSTRUMENT = "instrument"
    TOOL = "tool"
    OTHER = "other"
    UNKNOWN = "unknown"


class WearSlot(str, Enum):
    """Normalized equipment slot, not necessarily the item's physical form."""

    HEAD = "head"
    NECK = "neck"
    SHOULDERS = "shoulders"
    BACK = "back"
    TORSO = "torso"
    HANDS = "hands"
    WAIST = "waist"
    FEET = "feet"
    FINGER = "finger"
    ARMS = "arms"
    WEAPON_HAND = "weapon_hand"
    SHIELD_HAND = "shield_hand"
    ARMOR = "armor"
    NONE = "none"
    UNKNOWN = "unknown"


class Activation(str, Enum):
    PASSIVE = "passive"
    ACTION = "action"
    BONUS_ACTION = "bonus_action"
    REACTION = "reaction"
    FREE_ACTION = "free_action"
    COMMAND_WORD = "command_word"
    ON_HIT = "on_hit"
    WHEN_HIT = "when_hit"
    SPECIAL = "special"


class UsagePeriod(str, Enum):
    TURN = "turn"
    ROUND = "round"
    MINUTE = "minute"
    HOUR = "hour"
    DAY = "day"
    SHORT_REST = "short_rest"
    LONG_REST = "long_rest"
    SHORT_OR_LONG_REST = "short_or_long_rest"
    WEEK = "week"
    TOTAL = "total"
    OTHER = "other"


class RechargeTiming(str, Enum):
    DAWN = "dawn"
    DUSK = "dusk"
    MIDNIGHT = "midnight"
    SHORT_REST = "short_rest"
    LONG_REST = "long_rest"
    SHORT_OR_LONG_REST = "short_or_long_rest"
    ROLL = "roll"
    OTHER = "other"


class DurationUnit(str, Enum):
    TURN = "turn"
    ROUND = "round"
    MINUTE = "minute"
    HOUR = "hour"
    DAY = "day"
    PERMANENT = "permanent"
    UNTIL_DISPELLED = "until_dispelled"
    SPECIAL = "special"


class DistanceUnit(str, Enum):
    FEET = "feet"
    MILES = "miles"
    TOUCH = "touch"
    SELF = "self"
    SIGHT = "sight"
    UNLIMITED = "unlimited"
    SPECIAL = "special"


class AbilityScore(str, Enum):
    STR = "strength"
    DEX = "dexterity"
    CON = "constitution"
    INT = "intelligence"
    WIS = "wisdom"
    CHA = "charisma"


class CharacterClass(str, Enum):
    ARTIFICER = "artificer"
    BARBARIAN = "barbarian"
    BARD = "bard"
    CLERIC = "cleric"
    DRUID = "druid"
    FIGHTER = "fighter"
    MONK = "monk"
    PALADIN = "paladin"
    RANGER = "ranger"
    ROGUE = "rogue"
    SORCERER = "sorcerer"
    WARLOCK = "warlock"
    WIZARD = "wizard"


class DamageType(str, Enum):
    BLUDGEONING = "bludgeoning"
    PIERCING = "piercing"
    SLASHING = "slashing"
    FIRE = "fire"
    COLD = "cold"
    LIGHTNING = "lightning"
    THUNDER = "thunder"
    ACID = "acid"
    POISON = "poison"
    NECROTIC = "necrotic"
    RADIANT = "radiant"
    FORCE = "force"
    PSYCHIC = "psychic"


class DamageInteractionKind(str, Enum):
    DEALS = "deals"
    BONUS = "bonus"
    RESISTANCE = "resistance"
    IMMUNITY = "immunity"
    VULNERABILITY = "vulnerability"
    REDUCTION = "reduction"
    ABSORPTION = "absorption"
    OTHER = "other"


class Condition(str, Enum):
    BLINDED = "blinded"
    CHARMED = "charmed"
    DEAFENED = "deafened"
    FRIGHTENED = "frightened"
    GRAPPLED = "grappled"
    INCAPACITATED = "incapacitated"
    INVISIBLE = "invisible"
    PARALYZED = "paralyzed"
    PETRIFIED = "petrified"
    POISONED = "poisoned"
    PRONE = "prone"
    RESTRAINED = "restrained"
    STUNNED = "stunned"
    UNCONSCIOUS = "unconscious"
    EXHAUSTION = "exhaustion"


class ConditionEffectKind(str, Enum):
    INFLICTS = "inflicts"
    IMMUNITY = "immunity"
    RESISTANCE = "resistance"
    REMOVES = "removes"
    SUPPRESSES = "suppresses"
    TRIGGERED_BY = "triggered_by"
    OTHER = "other"


class CreatureType(str, Enum):
    ABERRATION = "aberration"
    BEAST = "beast"
    CELESTIAL = "celestial"
    CONSTRUCT = "construct"
    DRAGON = "dragon"
    ELEMENTAL = "elemental"
    FEY = "fey"
    FIEND = "fiend"
    GIANT = "giant"
    HUMANOID = "humanoid"
    MONSTROSITY = "monstrosity"
    OOZE = "ooze"
    PLANT = "plant"
    UNDEAD = "undead"


class EnvironmentTag(str, Enum):
    AIR = "air"
    AQUATIC = "aquatic"
    UNDERWATER = "underwater"
    WIND = "wind"
    DARKNESS = "darkness"
    DAYLIGHT = "daylight"
    FOREST = "forest"
    WILDERNESS = "wilderness"
    URBAN = "urban"
    ELEMENTAL_PLANE = "elemental_plane"
    OTHER = "other"


class TargetScope(str, Enum):
    SELF = "self"
    ALLY = "ally"
    ENEMY = "enemy"
    CREATURE = "creature"
    OBJECT = "object"
    AREA = "area"
    AURA = "aura"
    SPECIAL = "special"


class RequirementKind(str, Enum):
    CLASS = "class"
    RACE = "race"
    CREATURE_TYPE = "creature_type"
    ALIGNMENT = "alignment"
    LEVEL = "level"
    SKILL = "skill"
    PROFICIENCY = "proficiency"
    ABILITY_SCORE = "ability_score"
    ENVIRONMENT = "environment"
    OTHER = "other"


class ModifierKind(str, Enum):
    ARMOR_CLASS = "armor_class"
    ATTACK_ROLL = "attack_roll"
    DAMAGE_ROLL = "damage_roll"
    SAVING_THROW = "saving_throw"
    SPELL_SAVE_DC = "spell_save_dc"
    SPELL_ATTACK = "spell_attack"
    ABILITY_SCORE = "ability_score"
    ABILITY_CHECK = "ability_check"
    SKILL_CHECK = "skill_check"
    INITIATIVE = "initiative"
    SPEED = "speed"
    SENSE = "sense"
    HIT_POINTS = "hit_points"
    PROFICIENCY = "proficiency"
    OTHER = "other"


class EffectCategory(str, Enum):
    OFFENSE = "offense"
    DEFENSE = "defense"
    UTILITY = "utility"
    CONTROL = "control"
    HEALING = "healing"
    MOVEMENT = "movement"
    SENSE = "sense"
    TRANSFORMATION = "transformation"
    SUMMONING = "summoning"
    STORAGE = "storage"
    SOCIAL = "social"
    LUCK = "luck"
    ENVIRONMENTAL = "environmental"
    OTHER = "other"


class DrawbackKind(str, Enum):
    CURSE = "curse"
    SELF_DAMAGE = "self_damage"
    ABILITY_PENALTY = "ability_penalty"
    VULNERABILITY = "vulnerability"
    BEHAVIORAL = "behavioral"
    RESOURCE_COST = "resource_cost"
    DESTRUCTION_CONDITION = "destruction_condition"
    ATTUNEMENT_RISK = "attunement_risk"
    OTHER = "other"


# ---- Type normalizers --------------------------------------------------------


def _to_rarity(v: object) -> Rarity:
    if isinstance(v, Rarity):
        return v

    text = _normalize_key(v)
    text = text.replace("veryrare", "very rare")

    aliases = {
        "common": Rarity.COMMON,
        "uncommon": Rarity.UNCOMMON,
        "rare": Rarity.RARE,
        "very rare": Rarity.VERY_RARE,
        "legendary": Rarity.LEGENDARY,
        "artifact": Rarity.ARTIFACT,
        "artifacts": Rarity.ARTIFACT,
        "varies": Rarity.VARIES,
        "variable": Rarity.VARIES,
        "unknown": Rarity.UNKNOWN,
    }

    if text in aliases:
        return aliases[text]

    raise ValueError(f"unknown rarity: {v!r}")


def _to_item_category(v: object) -> ItemCategory:
    if isinstance(v, ItemCategory):
        return v

    text = _normalize_key(v)

    aliases = {
        "wondrous": ItemCategory.WONDROUS,
        "wondrous item": ItemCategory.WONDROUS,
        "weapon": ItemCategory.WEAPON,
        "armor": ItemCategory.ARMOR,
        "armour": ItemCategory.ARMOR,
        "shield": ItemCategory.ARMOR,  # Shields usually appear as Armor (shield).
        "ring": ItemCategory.RING,
        "rod": ItemCategory.ROD,
        "staff": ItemCategory.STAFF,
        "wand": ItemCategory.WAND,
        "potion": ItemCategory.POTION,
        "elixir": ItemCategory.POTION,
        "scroll": ItemCategory.SCROLL,
        "other": ItemCategory.OTHER,
        "unknown": ItemCategory.OTHER,
    }

    if text in aliases:
        return aliases[text]

    for member in ItemCategory:
        if text == member.value:
            return member

    raise ValueError(f"unknown item category: {v!r}")


def _to_item_form(v: object) -> ItemForm:
    if isinstance(v, ItemForm):
        return v

    text = _normalize_key(v)

    aliases = {
        "amulet": ItemForm.AMULET,
        "necklace": ItemForm.NECKLACE,
        "cloak": ItemForm.CLOAK,
        "cape": ItemForm.CAPE,
        "gown": ItemForm.GOWN,
        "robe": ItemForm.GOWN,
        "boots": ItemForm.BOOTS,
        "gloves": ItemForm.GLOVES,
        "belt": ItemForm.BELT,
        "helm": ItemForm.HELM,
        "helmet": ItemForm.HELMET,
        "mask": ItemForm.MASK,
        "crown": ItemForm.CROWN,
        "headband": ItemForm.HEADBAND,
        "bracers": ItemForm.BRACERS,
        "gauntlets": ItemForm.GAUNTLETS,
        "ring": ItemForm.RING,
        "armor": ItemForm.ARMOR,
        "armour": ItemForm.ARMOR,
        "shield": ItemForm.SHIELD,
        "dagger": ItemForm.DAGGER,
        "sword": ItemForm.SWORD,
        "great sword": ItemForm.GREATSWORD,
        "greatsword": ItemForm.GREATSWORD,
        "axe": ItemForm.AXE,
        "battle axe": ItemForm.BATTLEAXE,
        "battleaxe": ItemForm.BATTLEAXE,
        "mace": ItemForm.MACE,
        "war hammer": ItemForm.WARHAMMER,
        "warhammer": ItemForm.WARHAMMER,
        "war pick": ItemForm.WAR_PICK,
        "warpick": ItemForm.WAR_PICK,
        "razor": ItemForm.RAZOR,
        "staff": ItemForm.STAFF,
        "wand": ItemForm.WAND,
        "rod": ItemForm.ROD,
        "potion": ItemForm.POTION,
        "elixir": ItemForm.ELIXIR,
        "scroll": ItemForm.SCROLL,
        "chest": ItemForm.CHEST,
        "backpack": ItemForm.BACKPACK,
        "pouch": ItemForm.POUCH,
        "compass": ItemForm.COMPASS,
        "canteen": ItemForm.CANTEEN,
        "shovel": ItemForm.SHOVEL,
        "drum": ItemForm.DRUM,
        "horn": ItemForm.HORN,
        "pipe": ItemForm.PIPE,
        "caltrops": ItemForm.CALTROPS,
        "instrument": ItemForm.INSTRUMENT,
        "tool": ItemForm.TOOL,
        "other": ItemForm.OTHER,
        "unknown": ItemForm.UNKNOWN,
    }

    if text in aliases:
        return aliases[text]

    for member in ItemForm:
        if text == member.value:
            return member

    raise ValueError(f"unknown item form: {v!r}")


def _to_wear_slot(v: object) -> WearSlot:
    if isinstance(v, WearSlot):
        return v

    text = _normalize_key(v)

    aliases = {
        "head": WearSlot.HEAD,
        "face": WearSlot.HEAD,
        "hat": WearSlot.HEAD,
        "helm": WearSlot.HEAD,
        "helmet": WearSlot.HEAD,
        "mask": WearSlot.HEAD,
        "crown": WearSlot.HEAD,
        "headband": WearSlot.HEAD,
        "neck": WearSlot.NECK,
        "amulet": WearSlot.NECK,
        "necklace": WearSlot.NECK,
        "shoulders": WearSlot.SHOULDERS,
        "shoulder": WearSlot.SHOULDERS,
        "cloak": WearSlot.SHOULDERS,
        "cape": WearSlot.SHOULDERS,
        "back": WearSlot.BACK,
        "backpack": WearSlot.BACK,
        "torso": WearSlot.TORSO,
        "chest": WearSlot.TORSO,
        "body": WearSlot.TORSO,
        "robe": WearSlot.TORSO,
        "gown": WearSlot.TORSO,
        "armor": WearSlot.ARMOR,
        "armour": WearSlot.ARMOR,
        "hands": WearSlot.HANDS,
        "hand": WearSlot.HANDS,
        "gloves": WearSlot.HANDS,
        "gauntlets": WearSlot.HANDS,
        "waist": WearSlot.WAIST,
        "belt": WearSlot.WAIST,
        "feet": WearSlot.FEET,
        "boots": WearSlot.FEET,
        "finger": WearSlot.FINGER,
        "ring": WearSlot.FINGER,
        "arms": WearSlot.ARMS,
        "bracers": WearSlot.ARMS,
        "weapon": WearSlot.WEAPON_HAND,
        "weapon hand": WearSlot.WEAPON_HAND,
        "wield": WearSlot.WEAPON_HAND,
        "wielded": WearSlot.WEAPON_HAND,
        "held": WearSlot.WEAPON_HAND,
        "shield": WearSlot.SHIELD_HAND,
        "shield hand": WearSlot.SHIELD_HAND,
        "none": WearSlot.NONE,
        "not worn": WearSlot.NONE,
        "unknown": WearSlot.UNKNOWN,
    }

    if text in aliases:
        return aliases[text]

    for member in WearSlot:
        if text == member.value:
            return member
        if text == member.value.replace("_", " "):
            return member

    raise ValueError(f"unknown wear slot: {v!r}")


# ---- Annotated normalized types ---------------------------------------------


Label = Annotated[
    str,
    BeforeValidator(_to_label),
    Hint("Short normalized label or name."),
]

FreeText = Annotated[
    str,
    BeforeValidator(_to_text),
    Hint("Free-form source text or explanatory prose."),
]

RarityType = Annotated[
    Rarity,
    BeforeValidator(_to_rarity),
    Hint("Rarity tier: common, uncommon, rare, very rare, legendary, artifact, varies, unknown."),
]

ItemCategoryType = Annotated[
    ItemCategory,
    BeforeValidator(_to_item_category),
    Hint("Top-level category from the item header line, e.g. 'wondrous item', 'armor', 'weapon'."),
]

ItemFormType = Annotated[
    ItemForm,
    BeforeValidator(_to_item_form),
    Hint("Physical form/base object, e.g. amulet, mask, helm, shield, dagger, pouch."),
]

WearSlotType = Annotated[
    WearSlot,
    BeforeValidator(_to_wear_slot),
    Hint("Normalized equipment slot, e.g. head, neck, finger, shoulders, weapon_hand."),
]


# ---- Value objects -----------------------------------------------------------
# These are nested inside entities. They are not registered as standalone
# ontology tables unless the application later chooses to promote them.


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceRef(_Base):
    source_file: Optional[Label] = None
    pages: list[int] = Field(default_factory=list)
    page_start: Optional[int] = Field(default=None, ge=1)
    page_end: Optional[int] = Field(default=None, ge=1)
    note: Optional[FreeText] = None

    @model_validator(mode="after")
    def fill_page_range(self) -> "SourceRef":
        if self.pages:
            sorted_pages = sorted(set(self.pages))
            self.pages = sorted_pages

            if self.page_start is None:
                self.page_start = sorted_pages[0]

            if self.page_end is None:
                self.page_end = sorted_pages[-1]

        return self


class Requirement(_Base):
    kind: RequirementKind
    values: list[Label] = Field(default_factory=list)
    classes: list[CharacterClass] = Field(default_factory=list)
    notes: Optional[FreeText] = None


class Attunement(_Base):
    required: bool = False
    requirements: list[Requirement] = Field(default_factory=list)
    raw_text: Optional[FreeText] = None
    notes: Optional[FreeText] = None


class Distance(_Base):
    value: Optional[float] = None
    unit: DistanceUnit = DistanceUnit.FEET
    notes: Optional[FreeText] = None


class Duration(_Base):
    value: Optional[float] = None
    unit: Optional[DurationUnit] = None
    concentration: Optional[bool] = None
    notes: Optional[FreeText] = None


class Save(_Base):
    ability: AbilityScore
    dc: Optional[int] = Field(default=None, ge=1)
    notes: Optional[FreeText] = None


class Modifier(_Base):
    kind: ModifierKind
    value: Optional[float] = None
    value_text: Optional[Label] = None
    target: Optional[Label] = None
    ability: Optional[AbilityScore] = None
    duration: Optional[Duration] = None
    notes: Optional[FreeText] = None


class DamageInteraction(_Base):
    kind: DamageInteractionKind
    dice: Optional[Label] = None
    bonus: Optional[int] = None
    damage_type: Optional[DamageType] = None
    threshold: Optional[Label] = None
    notes: Optional[FreeText] = None


class ConditionEffect(_Base):
    kind: ConditionEffectKind
    condition: Condition
    notes: Optional[FreeText] = None


class Targeting(_Base):
    scope: Optional[TargetScope] = None
    range: Optional[Distance] = None
    creature_types: list[CreatureType] = Field(default_factory=list)
    creature_subtypes: list[Label] = Field(default_factory=list)
    environments: list[EnvironmentTag] = Field(default_factory=list)
    environment_details: list[Label] = Field(default_factory=list)
    requires_hearing: Optional[bool] = None
    requires_sight: Optional[bool] = None
    notes: Optional[FreeText] = None


class Recharge(_Base):
    amount: Optional[Label] = None
    timing: Optional[RechargeTiming] = None
    condition: Optional[FreeText] = None
    notes: Optional[FreeText] = None


class UsageLimit(_Base):
    uses: Optional[int] = Field(default=None, ge=1)
    uses_text: Optional[Label] = None
    per: Optional[UsagePeriod] = None
    activation: Optional[Activation] = None
    recharge: Optional[Recharge] = None
    total_uses: Optional[int] = Field(default=None, ge=1)
    condition: Optional[FreeText] = None
    notes: Optional[FreeText] = None


class SpellCast(_Base):
    spell_name: Label
    level: Optional[int] = Field(default=None, ge=0)
    charges_cost: Optional[int] = Field(default=None, ge=1)
    activation: Optional[Activation] = None
    save: Optional[Save] = None
    usage_limit: Optional[UsageLimit] = None
    notes: Optional[FreeText] = None


class ChargeSpend(_Base):
    cost: int = Field(ge=1)
    effect_name: Optional[Label] = None
    effect_summary: Optional[FreeText] = None
    spell: Optional[SpellCast] = None
    notes: Optional[FreeText] = None


class ItemText(_Base):
    rules: Optional[FreeText] = None
    lore: Optional[FreeText] = None
    raw: Optional[FreeText] = None


class ExtractionInfo(_Base):
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    needs_review: bool = False
    warnings: list[Label] = Field(default_factory=list)
    notes: Optional[FreeText] = None


# ---- Entities ---------------------------------------------------------------
# These classes are the main ontology objects. Relationship fields use UUIDs,
# matching the style of the Stormland Holdings example.


class MagicItem(_Base):
    """One sellable magic item in Reznar's catalog."""

    name: Label = Field(min_length=1)

    header_line: Optional[FreeText] = None
    item_category: Optional[ItemCategoryType] = None
    item_form: Optional[ItemFormType] = None
    subtype: Optional[Label] = None
    rarity: Optional[RarityType] = None

    wear_slot: Optional[WearSlotType] = None
    attunement: Attunement = Field(default_factory=Attunement)

    is_artifact: bool = False
    is_cursed: bool = False
    is_consumable: bool = False

    tags: list[Label] = Field(default_factory=list)
    text: ItemText = Field(default_factory=ItemText)
    source: SourceRef = Field(default_factory=SourceRef)
    extraction: ExtractionInfo = Field(default_factory=ExtractionInfo)

    @model_validator(mode="after")
    def normalize_flags_and_review_state(self) -> "MagicItem":
        if self.rarity == Rarity.ARTIFACT:
            self.is_artifact = True

        if self.item_category in {ItemCategory.POTION, ItemCategory.SCROLL}:
            self.is_consumable = True

        if self.text.raw is None:
            warning = "raw source text missing"
            if warning not in self.extraction.warnings:
                self.extraction.warnings.append(warning)
            self.extraction.needs_review = True

        return self


class ItemRarityVariant(_Base):
    """Conditional rarity for items whose rarity varies by option or material."""

    magic_item_id: Optional[UUID] = None
    condition: Label
    rarity: RarityType
    notes: Optional[FreeText] = None


class ItemEffect(_Base):
    """One mechanical effect, ability, spell option, or passive property of an item."""

    magic_item_id: Optional[UUID] = None

    name: Optional[Label] = None
    category: Optional[EffectCategory] = None
    activation: Optional[Activation] = None
    duration: Optional[Duration] = None
    usage_limit: Optional[UsageLimit] = None
    charges_cost: Optional[int] = Field(default=None, ge=1)

    target: Optional[Targeting] = None
    save: Optional[Save] = None

    modifiers: list[Modifier] = Field(default_factory=list)
    damage_interactions: list[DamageInteraction] = Field(default_factory=list)
    condition_effects: list[ConditionEffect] = Field(default_factory=list)
    spells: list[SpellCast] = Field(default_factory=list)

    description: Optional[FreeText] = None
    source: Optional[SourceRef] = None
    extraction: ExtractionInfo = Field(default_factory=ExtractionInfo)


class ItemChargePool(_Base):
    """A charge pool attached to a magic item."""

    magic_item_id: Optional[UUID] = None

    name: Optional[Label] = None
    max_charges: int = Field(ge=1)
    recharge: Optional[Recharge] = None
    spends: list[ChargeSpend] = Field(default_factory=list)

    notes: Optional[FreeText] = None
    source: Optional[SourceRef] = None
    extraction: ExtractionInfo = Field(default_factory=ExtractionInfo)


class ItemUsageLimit(_Base):
    """An item-level usage limit not specific to a single extracted effect."""

    magic_item_id: Optional[UUID] = None

    uses: Optional[int] = Field(default=None, ge=1)
    uses_text: Optional[Label] = None
    per: Optional[UsagePeriod] = None
    activation: Optional[Activation] = None
    recharge: Optional[Recharge] = None
    total_uses: Optional[int] = Field(default=None, ge=1)
    condition: Optional[FreeText] = None
    notes: Optional[FreeText] = None

    source: Optional[SourceRef] = None
    extraction: ExtractionInfo = Field(default_factory=ExtractionInfo)


class ItemDrawback(_Base):
    """A curse, risk, drawback, self-damage rule, or other negative property."""

    magic_item_id: Optional[UUID] = None

    kind: DrawbackKind
    trigger: Optional[FreeText] = None
    penalty: Optional[FreeText] = None
    removal_condition: Optional[FreeText] = None
    notes: Optional[FreeText] = None

    source: Optional[SourceRef] = None
    extraction: ExtractionInfo = Field(default_factory=ExtractionInfo)


class ItemLore(_Base):
    """Lore, history, named figures, factions, and destruction conditions."""

    magic_item_id: Optional[UUID] = None

    summary: Optional[FreeText] = None
    full_text: Optional[FreeText] = None
    origin: Optional[FreeText] = None
    named_figures: list[Label] = Field(default_factory=list)
    factions: list[Label] = Field(default_factory=list)
    destruction_condition: Optional[FreeText] = None
    notes: Optional[FreeText] = None

    source: Optional[SourceRef] = None
    extraction: ExtractionInfo = Field(default_factory=ExtractionInfo)


# ---- Registry ---------------------------------------------------------------
# The registry includes ontology entities only. Nested value objects above are
# used inside these entities but are not top-level records.

REGISTRY: dict[str, type[BaseModel]] = {
    "MagicItem": MagicItem,
    "ItemRarityVariant": ItemRarityVariant,
    "ItemEffect": ItemEffect,
    "ItemChargePool": ItemChargePool,
    "ItemUsageLimit": ItemUsageLimit,
    "ItemDrawback": ItemDrawback,
    "ItemLore": ItemLore,
}