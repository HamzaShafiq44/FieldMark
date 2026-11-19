"""Shared visual inspection prompts for vision providers."""

from collections.abc import Sequence

DEFAULT_SUPPORTED_TYPES = ("tomato", "apple", "banana", "orange")

FRUIT_VISUAL_GUIDANCE = (
    "For oranges, rot may appear as water-soaked, translucent, or dark brown, black, or gray "
    "patches; mold as fuzzy or powdery blue, green, white, or gray patches, including near the "
    "stem; bruises as localized flattened or dull-discolored areas; and deformities as severe "
    "wrinkling, visibly loose or puffy peel, or deep cracks. Minor irregularities or light "
    "scarring alone are not defects. "
    "For bananas, rot may appear as water-soaked, translucent, or darkened patches; mold as "
    "fuzzy or powdery white, gray, green, or black growth near the crown or damaged peel; bruises "
    "as localized, large, flat dark patches. Do not mistake small scattered ripening spots for "
    "bruises. Deep splits or severe shriveling are deformities; light rub marks alone are not. "
    "For apples, rot may appear as dark, sunken, or water-soaked patches; mold as fuzzy growth "
    "near the stem or blossom end; bruises as localized flattened or discolored areas. Severe "
    "wrinkling, deep cracks, or small sunken dark spots may be deformities. Minor russeting, "
    "scars, or shape irregularities alone are not defects. "
    "For tomatoes, rot may appear as water-soaked, translucent, sunken, or dark patches; mold as "
    "fuzzy or powdery growth near the stem scar or cracks; bruises as localized dark, sunken "
    "areas. Deep cracks, catfacing scars, or a dark sunken blossom-end patch may be deformities. "
    "Minor dry scars or healed cracks alone are not defects. "
)
RIPENESS_VISUAL_GUIDANCE = (
    "Ripeness is separate from spoilage and defects. Use these only as tentative visual context: "
    "oranges may shift from greenish toward orange or yellow-orange, but some ripe varieties keep "
    "green areas; peel shine, texture, and the stem button are not reliable by themselves. "
    "Apples may shift from a green background toward cream or yellow as they ripen, but blush, "
    "sheen, and background color vary by variety and finish. Bananas commonly change from green "
    "toward yellow, and small brown sugar spots can be normal ripening rather than bruises. "
    "Tomatoes often change from pale green toward their mature variety-specific color; green "
    "shoulders can remain on some varieties. Lighting and cultivar affect all these cues. "
    "Do not infer ripeness, flavor, or internal quality from color alone. Never label fruit "
    "rotten or defective only because it appears unripe; unripe fruit may still be fresh. "
)
_PROMPT_TEMPLATE = (
    "You inspect one photo for a product-grading app. The inspection categories are "
    "__SUPPORTED_TYPES__. First identify the main product in the photo. "
    "Reply with JSON only: "
    '{"produce_type": __PRODUCE_TYPE_VALUES__|"unknown", '
    '"detected_item": "short common name of the product", '
    '"item_is_fruit": true|false, '
    '"condition": "fresh"|"rotten"|"unsure", "confidence": 0..1, '
    '"visual_observation": "short description of visible evidence", '
    '"defect_types": ["bruise"|"rot"|"mold"|"deformity"], '
    '"severity": "none"|"low"|"medium"|"high", "bbox": [x1,y1,x2,y2] or null}. '
    "Set item_is_fruit to true for edible fruits and vegetables, including products outside the "
    "inspection categories, and put the common name in detected_item, such as guava or grapes. "
    "For anything that is not an edible fruit or vegetable, set item_is_fruit to false, "
    'produce_type to "unknown", condition to "unsure", and report no defects. '
    'Use produce_type "unknown" when the product is not one of the inspection categories. '
    "Distinguish products by visible shape and surface, not color alone: bananas are elongated "
    "and curved with a crown; oranges are round citrus with a dimpled rind. "
    'If these cues are not clear enough, use "unknown" rather than guess. '
    + FRUIT_VISUAL_GUIDANCE
    + RIPENESS_VISUAL_GUIDANCE
    + "This is photo-only: do not infer softness, firmness, odor, leakage, taste, or "
    "internal damage. "
    "Do not make food-safety claims or claim unseen mold spread. "
    "visual_observation must be one short sentence (240 characters maximum) describing only "
    "visible signs that support the condition and defect labels. Do not infer hidden causes, "
    "invent details, include confidence percentages, or explain grading rules. "
    "If no clear defect is visible, say so. "
    "bbox must tightly enclose the single most prominent visible defect, not the whole fruit. "
    "If multiple defects are visible, locate the clearest or most severe one. "
    "If defect_types is empty or severity is none, bbox MUST be null. "
    "Only return a bbox when at least one defect type is reported with severity low or higher. "
    "Return null when no defect is visible or its location cannot be identified clearly. "
    "Use normalized coordinates relative to the full image, with the origin at top left. "
    'Report only what is visible. Use "unsure" when condition is unclear. '
    "Never make food-safety claims."
)


def _format_type_list(supported_types: Sequence[str]) -> str:
    """Join category names into a readable list for the prompt."""
    types = [item.strip().lower() for item in supported_types if item.strip()]
    if not types:
        return ", ".join(DEFAULT_SUPPORTED_TYPES)
    if len(types) == 1:
        return types[0]
    return ", ".join(types[:-1]) + f", and {types[-1]}"


def get_system_prompt(supported_types: Sequence[str] | None = None) -> str:
    """Build the vision prompt for the configured inspection categories."""
    types = list(supported_types or DEFAULT_SUPPORTED_TYPES)
    quoted = "|".join(f'"{item}"' for item in types)
    return _PROMPT_TEMPLATE.replace("__SUPPORTED_TYPES__", _format_type_list(types)).replace(
        "__PRODUCE_TYPE_VALUES__", quoted
    )


INSPECTION_TEXT = "Identify the product, then inspect it for visible defects."
SYSTEM_PROMPT = get_system_prompt()

LOCALIZATION_TEMPLATE = (
    "You already analyzed this product photo and reported these visible defect(s): __DEFECTS__. "
    "Visible evidence from that analysis: __EVIDENCE__ "
    "Look at the same photo again and locate the single clearest or most severe instance of the "
    "reported defect. Reply with JSON only: "
    '{"bbox": [x1, y1, x2, y2]} where every coordinate is normalized to the full image (0 to 1) '
    "with the origin at the top left. The box must tightly enclose that defect, not the whole "
    'fruit. If the defect cannot be clearly located in this photo, reply {"bbox": null}. '
    "Do not report new defects, change the analysis, or include any other fields. "
)
LOCALIZATION_TEXT = "Locate the reported defect in this photo and return only its bounding box."


def get_localization_prompt(defects: str, evidence: str) -> str:
    """Build the second-stage prompt that asks only for defect coordinates."""
    return LOCALIZATION_TEMPLATE.replace("__DEFECTS__", defects or "visible spoilage").replace(
        "__EVIDENCE__", evidence or "No written description is available."
    )
