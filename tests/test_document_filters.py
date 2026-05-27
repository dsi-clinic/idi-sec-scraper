"""Tests for processor.document_filters."""

# Standard library imports
import datetime
import textwrap

# Third party imports
import pytest

# Application imports
from idi_sec_scraper.document_filters import (
    DocumentFilterConfig,
    FilterCondition,
    FormTypeConfig,
    load_document_filters,
    select_and_filter_documents,
)
from idi_sec_scraper.types import ParsedDocument


def _doc(*, seq="1", description="", filename="doc.htm", type="", url="https://example.com"):
    return ParsedDocument(seq=seq, description=description, filename=filename, type=type, url=url)


def _config(*form_type_items: tuple[str, str, list[list[FilterCondition]]]) -> DocumentFilterConfig:
    """Build a DocumentFilterConfig from (key, match, groups) tuples."""
    return DocumentFilterConfig(
        form_types={
            key: FormTypeConfig(match=match, documents=groups)
            for key, match, groups in form_type_items
        }
    )


# ---------------------------------------------------------------------------
# load_document_filters
# ---------------------------------------------------------------------------


class TestLoadDocumentFilters:
    """Tests for load_document_filters()."""

    def test_loads_form_type_keys(self, tmp_path):
        yaml_text = textwrap.dedent("""\
            form_types:
              10-K:
                match: "10-?K"
                documents: []
              8-K:
                match: "8-K"
                documents: []
        """)
        p = tmp_path / "document_filters.yaml"
        p.write_text(yaml_text)
        config = load_document_filters(str(p))

        assert set(config.form_types.keys()) == {"10-K", "8-K"}

    def test_loads_match_pattern(self, tmp_path):
        yaml_text = textwrap.dedent("""\
            form_types:
              10-K:
                match: "10-?K"
                documents: []
        """)
        p = tmp_path / "document_filters.yaml"
        p.write_text(yaml_text)
        config = load_document_filters(str(p))

        assert config.form_types["10-K"].match == "10-?K"

    def test_loads_condition_fields(self, tmp_path):
        yaml_text = textwrap.dedent("""\
            form_types:
              10-K:
                match: "10-?K"
                documents:
                  - all_of:
                      - field: type
                        operator: regex
                        value: "^EX-21"
        """)
        p = tmp_path / "document_filters.yaml"
        p.write_text(yaml_text)
        config = load_document_filters(str(p))

        groups = config.form_types["10-K"].documents
        assert len(groups) == 1
        cond = groups[0][0]
        assert cond.field == "type"
        assert cond.operator == "regex"
        assert cond.value == "^EX-21"

    def test_multiple_groups_loaded(self, tmp_path):
        yaml_text = textwrap.dedent("""\
            form_types:
              10-K:
                match: "10-?K"
                documents:
                  - all_of:
                      - field: type
                        operator: regex
                        value: "^EX-21"
                  - all_of:
                      - field: type
                        operator: regex
                        value: "^EX-8"
        """)
        p = tmp_path / "document_filters.yaml"
        p.write_text(yaml_text)
        config = load_document_filters(str(p))

        assert len(config.form_types["10-K"].documents) == 2

    def test_invalid_field_raises_value_error(self, tmp_path):
        yaml_text = textwrap.dedent("""\
            form_types:
              10-K:
                match: "10-?K"
                documents:
                  - all_of:
                      - field: seq
                        operator: exact
                        value: "1"
        """)
        p = tmp_path / "document_filters.yaml"
        p.write_text(yaml_text)

        with pytest.raises(ValueError, match="Invalid filter field"):
            load_document_filters(str(p))

    def test_invalid_operator_raises_value_error(self, tmp_path):
        yaml_text = textwrap.dedent("""\
            form_types:
              10-K:
                match: "10-?K"
                documents:
                  - all_of:
                      - field: type
                        operator: contains
                        value: "EX-21"
        """)
        p = tmp_path / "document_filters.yaml"
        p.write_text(yaml_text)

        with pytest.raises(ValueError, match="Invalid filter operator"):
            load_document_filters(str(p))

    def test_empty_documents_list(self, tmp_path):
        yaml_text = textwrap.dedent("""\
            form_types:
              8-K:
                match: "8-K"
                documents: []
        """)
        p = tmp_path / "document_filters.yaml"
        p.write_text(yaml_text)
        config = load_document_filters(str(p))

        assert config.form_types["8-K"].documents == []

    def test_cutoff_date_defaults_to_none(self, tmp_path):
        yaml_text = textwrap.dedent("""\
            form_types:
              8-K:
                match: "8-K"
                documents: []
        """)
        p = tmp_path / "document_filters.yaml"
        p.write_text(yaml_text)
        config = load_document_filters(str(p))

        assert config.form_types["8-K"].cutoff_date is None

    def test_explicit_cutoff_date_is_parsed(self, tmp_path):
        yaml_text = textwrap.dedent("""\
            form_types:
              8-K:
                match: "8-K"
                cutoff_date: "2020-06-15"
                documents: []
        """)
        p = tmp_path / "document_filters.yaml"
        p.write_text(yaml_text)
        config = load_document_filters(str(p))

        assert config.form_types["8-K"].cutoff_date == datetime.date(2020, 6, 15)

    def test_loads_real_config_file(self):
        config = load_document_filters("config/document_filters.yaml")

        assert "10-K" in config.form_types
        assert "8-K" in config.form_types
        assert "13F-HR" in config.form_types


# ---------------------------------------------------------------------------
# select_and_filter_documents
# ---------------------------------------------------------------------------


class TestSelectAndFilterDocuments:
    """Tests for select_and_filter_documents()."""

    def test_no_matching_form_type_returns_none_key_and_all_docs(self):
        config = _config()
        docs = [_doc(type="EX-21.1"), _doc(type="8-K")]
        key, result = select_and_filter_documents("20-F", docs, config)

        assert key is None
        assert result == docs

    def test_matching_form_type_returns_config_key(self):
        config = _config(("10-K", "10-?K", []))
        key, _ = select_and_filter_documents("10-K", [], config)

        assert key == "10-K"

    def test_empty_documents_list_returns_empty(self):
        config = _config(("10-K", "10-?K", [[FilterCondition("type", "regex", "^EX-21")]]))
        _, result = select_and_filter_documents("10-K", [], config)

        assert result == []

    def test_regex_operator_matches(self):
        config = _config(("10-K", "10-?K", [[FilterCondition("type", "regex", "^EX-21")]]))
        docs = [_doc(type="EX-21.1"), _doc(type="8-K")]
        _, result = select_and_filter_documents("10-K", docs, config)

        assert len(result) == 1
        assert result[0].type == "EX-21.1"

    def test_exact_operator_matches(self):
        config = _config(
            (
                "8-K",
                "8-K",
                [[FilterCondition("description", "exact", "Complete submission text file")]],
            )
        )
        docs = [
            _doc(description="Complete submission text file"),
            _doc(description="8-K"),
        ]
        _, result = select_and_filter_documents("8-K", docs, config)

        assert len(result) == 1
        assert result[0].description == "Complete submission text file"

    def test_exact_operator_no_partial_match(self):
        config = _config(
            (
                "8-K",
                "8-K",
                [[FilterCondition("description", "exact", "Complete submission text file")]],
            )
        )
        docs = [_doc(description="Complete submission")]
        _, result = select_and_filter_documents("8-K", docs, config)

        assert result == []

    def test_groups_are_or_ed(self):
        config = _config(
            (
                "10-K",
                "10-?K",
                [
                    [FilterCondition("type", "regex", "^EX-21")],
                    [FilterCondition("type", "regex", "^EX-8")],
                ],
            )
        )
        docs = [
            _doc(type="EX-21.1"),
            _doc(type="EX-8.01"),
            _doc(type="DEF 14A"),
        ]
        _, result = select_and_filter_documents("10-K", docs, config)

        assert len(result) == 2
        assert {d.type for d in result} == {"EX-21.1", "EX-8.01"}

    def test_conditions_within_group_are_and_ed(self):
        config = _config(
            (
                "13F-HR",
                "13F-HR",
                [
                    [
                        FilterCondition("description", "exact", "INFORMATION TABLE"),
                        FilterCondition("filename", "regex", "(?i).*\\.html$"),
                    ]
                ],
            )
        )
        docs = [
            _doc(description="INFORMATION TABLE", filename="table.html"),
            _doc(description="INFORMATION TABLE", filename="table.xml"),
            _doc(description="OTHER", filename="table.html"),
        ]
        _, result = select_and_filter_documents("13F-HR", docs, config)

        assert len(result) == 1
        assert result[0].filename == "table.html"

    def test_form_type_match_is_regex(self):
        config = _config(("10-K", "10-?K", [[FilterCondition("type", "regex", "^EX-21")]]))
        docs = [_doc(type="EX-21.1"), _doc(type="OTHER")]

        # "10K" (no dash) should also match the "10-?K" pattern
        _, result = select_and_filter_documents("10K", docs, config)

        assert len(result) == 1

    def test_form_type_uses_first_matching_config(self):
        # Both "10-?K" and "10-K/?" match "10-K/A"; only the first should be used
        config = _config(
            ("entry-a", "10-?K", [[FilterCondition("type", "exact", "EX-21")]]),
            ("entry-b", "10-K/?", [[FilterCondition("type", "exact", "EX-31")]]),
        )
        docs = [_doc(type="EX-21"), _doc(type="EX-31")]
        key, result = select_and_filter_documents("10-K/A", docs, config)

        assert key == "entry-a"
        assert len(result) == 1
        assert result[0].type == "EX-21"

    def test_no_groups_returns_all_documents(self):
        config = _config(("10-K", "10-?K", []))
        docs = [_doc(type="EX-21.1"), _doc(type="8-K")]
        _, result = select_and_filter_documents("10-K", docs, config)

        assert result == docs
