"""Pydantic v2 data models — source of truth for all tool inputs and outputs."""

from enum import Enum
from pydantic import BaseModel


class TaskType(str, Enum):
    BUG_FIX = "BUG_FIX"
    NEW_FEATURE = "NEW_FEATURE"
    REFACTOR = "REFACTOR"


class SymbolType(str, Enum):
    FILE = "FILE"
    FUNCTION = "FUNCTION"
    CLASS = "CLASS"
    METHOD = "METHOD"


class Language(str, Enum):
    PYTHON = "PYTHON"
    TYPESCRIPT = "TYPESCRIPT"


class EdgeType(str, Enum):
    CALLS = "CALLS"
    IMPORTS = "IMPORTS"
    COVERS = "COVERS"
    DEFINED_IN = "DEFINED_IN"


class RationaleItem(BaseModel):
    file_path: str
    rationale: str


class ExcludedNode(BaseModel):
    file_path: str
    reason: str


class ContextBundle(BaseModel):
    task_type: TaskType
    confidence: float
    low_confidence: bool = False
    token_estimate: int
    tokens_saved: int
    files: list[str]
    rationale: list[str]
    excluded: list[str] = []
    message: str | None = None


class RefreshResult(BaseModel):
    files_processed: int
    nodes_added: int
    nodes_removed: int
    elapsed_seconds: float


class ErrorBundle(BaseModel):
    error: str
    message: str
    files: list[str] = []
    rationale: list[str] = []
