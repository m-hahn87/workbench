from __future__ import annotations

import hashlib
import io
import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from workbench.domain.models import ItemModel, Project, parse_item


class MarkdownFormatError(ValueError):
    pass


@dataclass(slots=True)
class MarkdownDocument:
    frontmatter: CommentedMap
    body: str


def _yaml() -> YAML:
    yaml = YAML(typ="rt")
    yaml.allow_unicode = True
    yaml.preserve_quotes = True
    yaml.width = 100
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def _commented(value: Any, *, field_name: str | None = None) -> Any:
    if isinstance(value, dict):
        return CommentedMap(
            (key, _commented(item, field_name=key)) for key, item in value.items()
        )
    if isinstance(value, list):
        result = CommentedSeq(_commented(item) for item in value)
        if field_name in {"tags", "owners", "participants"}:
            result.fa.set_flow_style()
        return result
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, (datetime, date, UUID)):
        return value.isoformat() if not isinstance(value, UUID) else str(value)
    if isinstance(value, Enum):
        return value.value
    return value


def frontmatter_data(document: MarkdownDocument) -> dict[str, Any]:
    return _plain(document.frontmatter)


def normalize_body(body: str) -> str:
    lines = body.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).strip() + "\n"


class MarkdownStore:
    def parse(self, text: str) -> MarkdownDocument:
        text = text.lstrip("\ufeff")
        lines = text.splitlines(keepends=True)
        if not lines or lines[0].strip() != "---":
            raise MarkdownFormatError(
                "file must start with a YAML frontmatter delimiter"
            )

        end = next(
            (
                index
                for index, line in enumerate(lines[1:], start=1)
                if line.strip() == "---"
            ),
            None,
        )
        if end is None:
            raise MarkdownFormatError(
                "YAML frontmatter is missing its closing delimiter"
            )

        try:
            frontmatter = _yaml().load("".join(lines[1:end]))
        except Exception as exc:
            raise MarkdownFormatError(f"invalid YAML frontmatter: {exc}") from exc
        if not isinstance(frontmatter, CommentedMap):
            raise MarkdownFormatError("YAML frontmatter must be a mapping")
        return MarkdownDocument(frontmatter=frontmatter, body="".join(lines[end + 1 :]))

    def render(self, document: MarkdownDocument) -> str:
        output = io.StringIO()
        _yaml().dump(document.frontmatter, output)
        body = document.body
        if body and not body.startswith("\n"):
            body = "\n" + body
        if body and not body.endswith("\n"):
            body += "\n"
        return f"---\n{output.getvalue()}---\n{body}"

    def from_model(self, model: BaseModel, body: str = "") -> MarkdownDocument:
        data = model.model_dump(mode="json", exclude_none=True)
        return MarkdownDocument(frontmatter=_commented(data), body=body)

    def read(self, path: Path) -> MarkdownDocument:
        return self.parse(path.read_text(encoding="utf-8"))

    def write(self, path: Path, document: MarkdownDocument) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(self.render(document), encoding="utf-8", newline="\n")
        os.replace(temporary, path)

    def read_item(self, path: Path) -> tuple[ItemModel, MarkdownDocument]:
        document = self.read(path)
        return parse_item(frontmatter_data(document)), document

    def read_project(self, path: Path) -> tuple[Project, MarkdownDocument]:
        document = self.read(path)
        return Project.model_validate(frontmatter_data(document)), document

    def semantic_hash(self, model: BaseModel, body: str = "") -> str:
        normalized = {
            "frontmatter": model.model_dump(mode="json", exclude_none=False),
            "body": normalize_body(body),
        }
        payload = json.dumps(
            normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"

    def item_hash(self, document: MarkdownDocument) -> str:
        item = parse_item(frontmatter_data(document))
        return self.semantic_hash(item, document.body)
