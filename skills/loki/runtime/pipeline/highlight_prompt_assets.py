"""Fail-closed loader for versioned Highlight prompt assets."""

from __future__ import annotations

from dataclasses import dataclass, replace

import hashlib

import json

from pathlib import Path

from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]

_PROMPT_ROOT = _REPO_ROOT / "prompts" / "highlight"

_REQUIRED_METADATA = {
    "name",
    "version",
    "status",
    "language",
    "required_inputs",
    "eval",
}

@dataclass(frozen=True)
class HighlightPromptAsset:
    relative_path: str
    name: str
    version: str
    status: str
    language: str
    required_inputs: tuple[str, ...]
    eval_command: str
    body: str
    body_sha256: str
    supplement_path: str | None = None

def _canonical_asset_path(relative_path: str) -> Path:
    candidate = Path(relative_path)
    if candidate.is_absolute() or not candidate.parts:
        raise ValueError("Highlight prompt asset path must be repo-relative")
    resolved = (_REPO_ROOT / candidate).resolve(strict=True)
    if not resolved.is_relative_to(_PROMPT_ROOT.resolve(strict=True)):
        raise ValueError("Highlight prompt asset path escapes prompts/highlight")
    current = _REPO_ROOT
    for part in candidate.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Highlight prompt asset path contains a symlink")
        attributes = getattr(current.stat(), "st_file_attributes", 0)
        if attributes & 0x400:
            raise ValueError("Highlight prompt asset path contains a reparse point")
    return resolved

def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        raise ValueError("Highlight prompt asset is missing frontmatter")
    delimiter = text.find("\n---\n", 4)
    if delimiter < 0:
        raise ValueError("Highlight prompt asset frontmatter is not terminated")
    metadata_text = text[4:delimiter]
    body = text[delimiter + len("\n---\n"):]
    try:
        metadata = json.loads(metadata_text)
    except json.JSONDecodeError as exc:
        raise ValueError("Highlight prompt asset frontmatter must be JSON") from exc
    if not isinstance(metadata, dict):
        raise ValueError("Highlight prompt asset frontmatter must be an object")
    return metadata, body

def _validated_metadata(
    metadata: dict[str, Any],
) -> tuple[str, str, str, str, tuple[str, ...], str]:
    missing = sorted(_REQUIRED_METADATA - set(metadata))
    if missing:
        raise ValueError(f"Highlight prompt asset metadata missing: {', '.join(missing)}")
    name = metadata.get("name")
    version = metadata.get("version")
    status = metadata.get("status")
    language = metadata.get("language")
    required_inputs = metadata.get("required_inputs")
    evaluation = metadata.get("eval")
    if not all(
        isinstance(value, str) and value.strip()
        for value in (name, version, status, language)
    ):
        raise ValueError("Highlight prompt asset string metadata is invalid")
    if status not in {"shadow", "candidate"}:
        raise ValueError("Highlight prompt asset status must be shadow or candidate")
    if language not in {"en", "ko"}:
        raise ValueError("Highlight prompt asset language must be en or ko")
    if not isinstance(required_inputs, list) or not required_inputs or not all(
        isinstance(value, str) and value.strip() for value in required_inputs
    ):
        raise ValueError("Highlight prompt asset required_inputs is invalid")
    if (
        not isinstance(evaluation, dict)
        or not isinstance(evaluation.get("command"), str)
        or not evaluation["command"].strip()
    ):
        raise ValueError("Highlight prompt asset eval.command is invalid")
    return (
        name.strip(),
        version.strip(),
        status.strip(),
        language.strip(),
        tuple(value.strip() for value in required_inputs),
        evaluation["command"].strip(),
    )

def load_highlight_prompt_asset(relative_path: str) -> HighlightPromptAsset:
    path = _canonical_asset_path(relative_path)
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Highlight prompt asset must be UTF-8") from exc



    text = text.replace("\r\n", "\n").replace("\r", "\n")
    metadata, body = _split_frontmatter(text)
    if not body:
        raise ValueError("Highlight prompt asset body is empty")
    name, version, status, language, required_inputs, eval_command = _validated_metadata(
        metadata
    )
    return HighlightPromptAsset(
        relative_path=path.relative_to(_REPO_ROOT).as_posix(),
        name=name,
        version=version,
        status=status,
        language=language,
        required_inputs=required_inputs,
        eval_command=eval_command,
        body=body,
        body_sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
    )

_CHRONOLOGICAL_AUTHOR_GUIDANCE = """
장면을 고르기 전에 전편 원문 개요를 시작부터 끝까지 시간순으로 살펴, 조용한 구간과 방송 말미까지 큰 활동과 결과를 파악한다. 이전 관찰이나 지도가 있다면 탐색 자료로 활용하고, 지도가 없으면 같은 작성자가 원문에서 초안을 직접 만든다. 개요가 없으면 기존 시간순 원문 조회로 큰 흐름을 파악한다. 표본 사이의 내용은 실제 원문으로 확인한다.

제출 전 전체 D1/D2를 전편 흐름과 대조한다. 방송 이해를 바꾸는 선택·이유·결과는 필요한 앞뒤 원문을 확인해 해당 활동에 남긴다. 반응 순위와 피크는 위치를 찾는 단서다. Point와 Highlight는 실제 장면의 앞말·응수·결과를 확인한 뒤 재미가 제목에서도 전달되게 쓴다. 판단이 달라지는 경우 실제 화면과 원음을 확인하고, 끝내 확인할 수 없는 부분은 기존 불확실성 필드에 남긴다. 대기·반복과 근거 없는 공백은 비워도 된다.
""".strip()

def load_full_author_prompt(author_prompt_profile: str | None = None, *,
                            chronological_overview: bool = False) -> HighlightPromptAsset:
    """Share the bounded full-author selection without unrelated stage assets."""
    supplements = {
        'evidence_retention_d2_v1': 'prompts/highlight/editorial_full_author_302_d2.md',
        'evidence_retention_d2_v2': 'prompts/highlight/editorial_full_author_302_d2_outcomes.md',
        'evidence_retention_d2_v3': 'prompts/highlight/editorial_full_author_302_d2_3092.md',
    }
    if author_prompt_profile not in (None, 'evidence_retention_v1', *supplements):
        raise ValueError('unknown_author_prompt_profile')
    if author_prompt_profile == 'evidence_retention_d2_v3':
        path = 'prompts/highlight/editorial_full_author_302_retention_3092.md'
    elif author_prompt_profile:
        path = 'prompts/highlight/editorial_full_author_302_retention.md'
    else:
        path = 'prompts/highlight/editorial_full_author_302.md'
    asset = load_highlight_prompt_asset(path)
    body = asset.body
    supplement_path = supplements.get(author_prompt_profile)
    if supplement_path:
        supplement = _canonical_asset_path(supplement_path).read_text(encoding="utf-8")
        body = body.rstrip("\n") + "\n\n" + supplement
    if chronological_overview:
        body = body.rstrip("\n") + "\n\n" + _CHRONOLOGICAL_AUTHOR_GUIDANCE + "\n"
    if body == asset.body:
        return asset
    return replace(asset, body=body, body_sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
                   supplement_path=supplement_path)
