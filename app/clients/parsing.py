from __future__ import annotations

import hashlib
import html as html_module
import json
import re
import unicodedata
from collections.abc import Iterator, Mapping

from selectolax.parser import HTMLParser

from ..domain.models import SearchItem, SourceDocument


class ParseError(ValueError):
    """Raised when a JDIHN response no longer matches its public page contract."""


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE).split())


def matched_keywords(
    text: str, include_keywords: tuple[str, ...], exclude_keywords: tuple[str, ...]
) -> tuple[str, ...]:
    normalized = normalize_text(text)
    if any(normalize_text(keyword) in normalized for keyword in exclude_keywords):
        return ()
    return tuple(keyword for keyword in include_keywords if normalize_text(keyword) in normalized)


def _flight_payloads(page_html: str) -> Iterator[str]:
    tree = HTMLParser(page_html)
    prefix = "self.__next_f.push("
    for script in tree.css("script"):
        content = script.text(deep=True).strip()
        if not content.startswith(prefix) or not content.endswith(")"):
            continue
        try:
            payload = json.loads(content[len(prefix) : -1])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, list) and len(payload) >= 2 and isinstance(payload[1], str):
            yield payload[1]


def _extract_json_object(content: str, marker: str, start_at: int = 0) -> tuple[object, int] | None:
    marker_index = content.find(marker, start_at)
    if marker_index < 0:
        return None
    object_start = content.find("{", marker_index + len(marker))
    if object_start < 0:
        return None

    depth = 0
    in_string = False
    escaped = False
    for index in range(object_start, len(content)):
        character = content[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                raw = content[object_start : index + 1]
                try:
                    return json.loads(raw), index + 1
                except json.JSONDecodeError:
                    return None
    return None


def _as_mapping(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, Mapping) else None


def _optional_string(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def parse_search_page(page_html: str) -> list[SearchItem]:
    for payload in _flight_payloads(html_module.unescape(page_html)):
        extracted = _extract_json_object(payload, '"initialData":')
        if extracted is None:
            continue
        initial_data = _as_mapping(extracted[0])
        results = _as_mapping(initial_data.get("results")) if initial_data else None
        items = results.get("items") if results else None
        if not isinstance(items, list):
            continue

        parsed: list[SearchItem] = []
        for raw_item in items:
            item = _as_mapping(raw_item)
            if item is None:
                continue
            source_id = _optional_int(item.get("id"))
            title = _optional_string(item.get("title"))
            category = _optional_string(item.get("category"))
            if source_id is None or title is None or category is None:
                continue
            parsed.append(
                SearchItem(
                    source_id=str(source_id),
                    title=title,
                    document_number=_optional_string(item.get("documentNumber")),
                    category=category,
                    subtitle=_optional_string(item.get("subtitle")),
                    description=_optional_string(item.get("description")),
                    institution=_optional_string(item.get("instansi")),
                    effective_status=_optional_string(item.get("status")),
                    law_type=_optional_string(item.get("regulationType")),
                    law_year=_optional_int(item.get("tahunTerbit")),
                )
            )
        return parsed
    raise ParseError("Payload initialData hasil pencarian JDIHN tidak ditemukan")


def _structured_data(page_html: str) -> Iterator[Mapping[str, object]]:
    tree = HTMLParser(page_html)
    for script in tree.css('script[type="application/ld+json"]'):
        try:
            value = json.loads(script.text(deep=True))
        except json.JSONDecodeError:
            continue
        if isinstance(value, Mapping):
            yield value
        elif isinstance(value, list):
            for item in value:
                mapping = _as_mapping(item)
                if mapping is not None:
                    yield mapping


def _find_structured_attachment(page_html: str) -> str | None:
    for value in _structured_data(page_html):
        for key in ("contentUrl", "encodingUrl", "url"):
            candidate = _optional_string(value.get(key))
            if candidate and ".pdf" in candidate.casefold():
                return candidate
        encoding = _as_mapping(value.get("encoding"))
        if encoding:
            candidate = _optional_string(encoding.get("contentUrl"))
            if candidate:
                return candidate
    return None


def _law_number(document_number: str | None) -> str | None:
    if not document_number:
        return None
    match = re.search(r"\bNomor\s+([A-Za-z0-9./-]+)(?:\s+Tahun\s+\d{4})?", document_number, re.I)
    return match.group(1) if match else None


def parse_detail_page(
    page_html: str,
    search_item: SearchItem,
    source_url: str,
    include_keywords: tuple[str, ...],
    exclude_keywords: tuple[str, ...],
) -> SourceDocument:
    document: Mapping[str, object] | None = None
    unescaped = html_module.unescape(page_html)
    for payload in _flight_payloads(unescaped):
        offset = 0
        while True:
            extracted = _extract_json_object(payload, '"document":', offset)
            if extracted is None:
                break
            candidate = _as_mapping(extracted[0])
            offset = extracted[1]
            if (
                candidate
                and str(candidate.get("id")) == search_item.source_id
                and ("canonicalTitle" in candidate or "attachmentUrl" in candidate)
            ):
                document = candidate
                break
        if document is not None:
            break
    if document is None:
        raise ParseError(f"Payload detail JDIHN {search_item.source_id} tidak ditemukan")

    title = (
        _optional_string(document.get("canonicalTitle"))
        or _optional_string(document.get("title"))
        or search_item.title
    )
    document_number = (
        _optional_string(document.get("documentNumber")) or search_item.document_number
    )
    attachment_url = _find_structured_attachment(page_html) or _optional_string(
        document.get("attachmentUrl")
    )
    law_type = _optional_string(document.get("type")) or search_item.law_type
    effective_status = (
        _optional_string(document.get("effectiveStatus")) or search_item.effective_status
    )
    enacted_date = _optional_string(document.get("enactedDate"))
    enacted_year = _optional_int(enacted_date[:4]) if enacted_date else None
    law_year = search_item.law_year or enacted_year
    combined_text = " ".join(
        value
        for value in (
            title,
            document_number,
            search_item.subtitle,
            search_item.description,
            search_item.institution,
            law_type,
        )
        if value
    )
    matches = matched_keywords(combined_text, include_keywords, exclude_keywords)
    fingerprint_payload = json.dumps(
        {
            "attachment_url": attachment_url,
            "document_number": document_number,
            "effective_status": effective_status,
            "law_type": law_type,
            "law_year": law_year,
            "title": title,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return SourceDocument(
        source_id=search_item.source_id,
        source_url=source_url,
        title=title,
        attachment_url=attachment_url,
        category=search_item.category,
        document_number=document_number,
        law_type=law_type,
        law_number=_law_number(document_number),
        law_year=law_year,
        effective_status=effective_status,
        matched_keywords=matches,
        fingerprint=hashlib.sha256(fingerprint_payload.encode()).hexdigest(),
        institution=search_item.institution,
    )
