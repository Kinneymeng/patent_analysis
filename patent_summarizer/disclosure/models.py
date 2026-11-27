from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Mapping, Protocol, Any


class DisclosureChatClient(Protocol):
    def chat(self, system_prompt: str, user_prompt: str) -> str | Mapping[str, Any]:
        ...


class ConflictPolicy(str, Enum):
    ERROR = "error"
    SKIP = "skip"
    OVERWRITE = "overwrite"


class PriorArtStatus(str, Enum):
    DISABLED = "disabled"
    SUCCESS = "success"
    EMPTY = "empty"
    PARTIAL = "partial"
    FAILED = "failed"


class ValidationSeverity(str, Enum):
    WARNING = "warning"
    FATAL = "fatal"


@dataclass(frozen=True)
class DisclosureDiagnostic:
    stage: str
    code: str
    message: str
    action: str = ""
    severity: ValidationSeverity = ValidationSeverity.WARNING
    requires_review: bool = False


@dataclass(frozen=True)
class EvidenceReference:
    path: str
    locator: str
    excerpt: str
    kind: str = "material"


@dataclass(frozen=True)
class MaterialDocument:
    path: Path
    kind: str
    priority: int
    status: str
    text: str = ""
    evidence: tuple[EvidenceReference, ...] = ()
    image_references: tuple[str, ...] = ()
    message: str = ""
    truncated: bool = False


@dataclass(frozen=True)
class MaterialScanResult:
    roots: tuple[Path, ...]
    documents: tuple[MaterialDocument, ...]

    @property
    def readable(self) -> tuple[MaterialDocument, ...]:
        return tuple(item for item in self.documents if item.status == "read" and item.text.strip())

    @property
    def failed(self) -> tuple[MaterialDocument, ...]:
        return tuple(item for item in self.documents if item.status == "failed")

    @property
    def unsupported(self) -> tuple[MaterialDocument, ...]:
        return tuple(item for item in self.documents if item.status == "unsupported")


@dataclass(frozen=True)
class DisclosureSourceItem:
    source_id: str
    label: str
    summary: str
    context: str
    evidence: tuple[EvidenceReference, ...] = ()


@dataclass(frozen=True)
class InventionCandidate:
    candidate_id: str
    title: str
    technical_problem: str
    innovation: str
    feasibility: str
    differences: str = ""
    evidence_refs: tuple[str, ...] = ()
    recommended: bool = False
    recommendation_reason: str = ""


@dataclass(frozen=True)
class TitleProposal:
    source: DisclosureSourceItem
    titles: list[str]
    candidates: tuple[InventionCandidate, ...] = ()


@dataclass(frozen=True)
class TitleSelection:
    source_id: str
    titles: list[str]


@dataclass(frozen=True)
class DisclosureTitleReviewResult:
    selections: list[TitleSelection]
    conflict_policy: ConflictPolicy


DisclosureTitleReviewCallback = Callable[
    [list[TitleProposal], Path], DisclosureTitleReviewResult | None
]


@dataclass(frozen=True)
class PriorArtRecord:
    publication_number: str
    title: str
    abstract: str
    ipc_codes: tuple[str, ...]
    url: str
    source: str = "国家知识产权局专利公布公告系统"


@dataclass(frozen=True)
class PriorArtResult:
    status: PriorArtStatus
    searched_at: str = ""
    keywords: tuple[str, ...] = ()
    ipc_codes: tuple[str, ...] = ()
    records: tuple[PriorArtRecord, ...] = ()
    message: str = ""

    @classmethod
    def disabled(cls) -> "PriorArtResult":
        return cls(PriorArtStatus.DISABLED, message="未启用查新")


@dataclass(frozen=True)
class ModuleDefinition:
    name: str
    responsibility: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]


@dataclass(frozen=True)
class ParameterDefinition:
    name: str
    symbol: str
    meaning: str
    value_or_range: str
    basis: str
    is_example: bool = False


@dataclass(frozen=True)
class DisclosurePreview:
    source_id: str
    title: str
    technical_problems: tuple[str, ...]
    modules: tuple[ModuleDefinition, ...]
    process_steps: tuple[str, ...]
    key_points: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    suggested_parameters: tuple[ParameterDefinition, ...]
    assumptions: tuple[str, ...]
    open_questions: tuple[str, ...]
    prior_art: PriorArtResult = field(default_factory=PriorArtResult.disabled)


DisclosurePreviewReviewCallback = Callable[[DisclosurePreview], DisclosurePreview | None]


class PriorArtFailureAction(str, Enum):
    RETRY = "retry"
    SKIP = "skip"
    CANCEL = "cancel"


PriorArtFailureCallback = Callable[[str, str], PriorArtFailureAction]


@dataclass(frozen=True)
class DisclosureSubsection:
    number: str
    title: str
    paragraphs: tuple[str, ...]


@dataclass(frozen=True)
class DisclosureSection:
    number: str
    title: str
    paragraphs: tuple[str, ...] = ()
    subsections: tuple[DisclosureSubsection, ...] = ()


@dataclass(frozen=True)
class DisclosureDiagram:
    section_number: str
    kind: str
    title: str
    mermaid: str
    image_path: Path | None = None
    drawio_path: Path | None = None
    layout: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class DisclosureTable:
    section_number: str
    title: str
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class DisclosureFormula:
    section_number: str
    label: str
    latex: str
    description: str
    origin: str = "agent"
    source_ref: str = ""


@dataclass(frozen=True)
class TechnicalDisclosure:
    title: str
    abstract: str
    content_paragraphs: list[str] = field(default_factory=list)
    sections: tuple[DisclosureSection, ...] = ()
    diagrams: tuple[DisclosureDiagram, ...] = ()
    tables: tuple[DisclosureTable, ...] = ()
    formulas: tuple[DisclosureFormula, ...] = ()
    prior_art_status: PriorArtStatus = PriorArtStatus.DISABLED
    diagnostics: tuple[DisclosureDiagnostic, ...] = ()

    @property
    def review_items(self) -> tuple[DisclosureDiagnostic, ...]:
        return tuple(item for item in self.diagnostics if item.requires_review)

    @property
    def is_structured(self) -> bool:
        return bool(self.sections)

    def all_paragraphs(self) -> list[str]:
        if not self.sections:
            return list(self.content_paragraphs)
        result: list[str] = []
        for section in self.sections:
            result.extend(section.paragraphs)
            for subsection in section.subsections:
                result.extend(subsection.paragraphs)
        return result


@dataclass(frozen=True)
class DisclosureFailure:
    source_id: str
    title: str
    error: str


@dataclass(frozen=True)
class DisclosureGenerationResult:
    written: list[Path]
    skipped: list[Path]
    failures: list[DisclosureFailure]
    warnings: list[DisclosureDiagnostic] = field(default_factory=list)


@dataclass(frozen=True)
class DisclosureOutputPaths:
    docx: Path
    markdown: Path
    assets_dir: Path
    diagnostics: tuple[DisclosureDiagnostic, ...] = ()
    is_review_draft: bool = False


@dataclass(frozen=True)
class DisclosureCheckpoint:
    input_hash: str
    model: str
    prompt_version: str
    source_id: str
    title: str
    stage: str
    payload: Mapping[str, Any]
