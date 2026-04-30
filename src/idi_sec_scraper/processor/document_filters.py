"""Document filter configuration loading and application."""

# Standard library imports
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

# Third party imports
import yaml

# Application imports
from idi_sec_scraper.processor.types import ParsedDocument

_FILTERABLE_FIELDS = frozenset({"description", "filename", "type"})


@dataclass
class FilterCondition:
    """A single condition applied to one field of a document."""

    field: Literal["description", "filename", "type"]
    operator: Literal["regex", "exact"]
    value: str
    compiled_pattern: re.Pattern[str] | None = field(init=False, default=None, repr=False)

    def __post_init__(self) -> None:
        """Compile regex patterns once for fast matching."""
        if self.operator == "regex":
            self.compiled_pattern = re.compile(self.value)


@dataclass
class FormTypeConfig:
    """Filter configuration for a single form type."""

    match: str
    documents: list[list[FilterCondition]] = field(default_factory=list)
    compiled_match: re.Pattern[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Compile the form-type matcher once."""
        self.compiled_match = re.compile(self.match)


@dataclass
class DocumentFilterConfig:
    """Top-level configuration mapping form type keys to their filter rules."""

    form_types: dict[str, FormTypeConfig] = field(default_factory=dict)


def load_document_filters(path: str) -> DocumentFilterConfig:
    """Load a document filter configuration from a YAML file.

    Args:
        path: Filesystem path to the YAML configuration file.

    Returns:
        Parsed :class:`DocumentFilterConfig`.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the YAML structure is invalid.
    """
    with Path(path).open() as f:
        raw = yaml.safe_load(f)

    form_types: dict[str, FormTypeConfig] = {}
    for key, ft_raw in (raw or {}).get("form_types", {}).items():
        groups = []
        for group_raw in ft_raw.get("documents", []):
            conditions = []
            for cond_raw in group_raw.get("all_of", []):
                field_name = cond_raw["field"]
                operator = cond_raw["operator"]
                value = cond_raw["value"]
                if field_name not in _FILTERABLE_FIELDS:
                    raise ValueError(f"Invalid filter field: {field_name!r}")
                if operator not in ("regex", "exact"):
                    raise ValueError(f"Invalid filter operator: {operator!r}")
                conditions.append(FilterCondition(field=field_name, operator=operator, value=value))
            groups.append(conditions)
        form_types[key] = FormTypeConfig(match=ft_raw["match"], documents=groups)

    return DocumentFilterConfig(form_types=form_types)


def select_and_filter_documents(
    form_type: str,
    documents: list[ParsedDocument],
    config: DocumentFilterConfig,
) -> tuple[str | None, list[ParsedDocument]]:
    """Select form-type config and return filtered documents for that form.

    Returns:
        A tuple of ``(configured_form_type_key_or_none, filtered_documents)``.
    """
    entry = find_form_type_entry(form_type, config)
    if entry is None:
        return None, list(documents)
    _, ft_config = entry
    if not ft_config.documents:
        return entry[0], list(documents)
    return entry[0], [doc for doc in documents if _document_matches(doc, ft_config.documents)]


def find_form_type_entry(
    form_type: str, config: DocumentFilterConfig
) -> tuple[str, FormTypeConfig] | None:
    """Return first matching (form_type_key, FormTypeConfig) entry, else None."""
    for form_type_key, ft_config in config.form_types.items():
        if ft_config.compiled_match.match(form_type):
            return form_type_key, ft_config
    return None


def _document_matches(doc: ParsedDocument, groups: list[list[FilterCondition]]) -> bool:
    return any(_group_matches(doc, group) for group in groups)


def _group_matches(doc: ParsedDocument, conditions: list[FilterCondition]) -> bool:
    return all(_condition_matches(doc, cond) for cond in conditions)


def _condition_matches(doc: ParsedDocument, cond: FilterCondition) -> bool:
    value = getattr(doc, cond.field)
    if cond.operator == "regex":
        pattern = cond.compiled_pattern or re.compile(cond.value)
        return bool(pattern.match(value))
    if cond.operator == "exact":
        return value == cond.value
    return False
