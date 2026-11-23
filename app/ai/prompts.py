"""Shared visual inspection prompts, loaded from YAML configuration.

The public names are kept stable so providers and tests do not need to know
where the text is stored. See ``app/ai/prompt_config.py`` for the loader.
"""

from collections.abc import Sequence

from app.ai.prompt_config import load_prompt, render

_inspection = load_prompt("inspection")

DEFAULT_SUPPORTED_TYPES = tuple(_inspection.supported_products)


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
    types = [item.strip().lower() for item in (supported_types or DEFAULT_SUPPORTED_TYPES)]
    quoted = "|".join(f'"{item}"' for item in types)
    return render(
        _inspection.system,
        supported_products=_format_type_list(types),
        produce_type_values=quoted,
    )


INSPECTION_TEXT = _inspection.user
SYSTEM_PROMPT = get_system_prompt()

LOCALIZATION_TEXT = load_prompt("localization").user


def get_localization_prompt(defects: str, evidence: str) -> str:
    """Build the second-stage prompt that asks only for defect coordinates."""
    return render(
        load_prompt("localization").system,
        defects=defects or "visible spoilage",
        evidence=evidence or "No written description is available.",
    )
