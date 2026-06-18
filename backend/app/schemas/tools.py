# 工具 I/O 契约（A 定，B 实现 run_pytest 执行）。对齐 系统设计.md §6
# 所有工具输入输出必须过 Pydantic 校验，不允许返回一坨日志文本
from typing import Literal, Optional

from pydantic import BaseModel, Field

# 失败类型：评测捕获率只认 assertion；import/collection/fixture/timeout/error 都不算捕获
FailureType = Literal["assertion", "error", "import", "collection", "fixture", "timeout"]
# pytest 单用例结局；error 表示 setup/teardown 阶段出错
CaseStatus = Literal["passed", "failed", "skipped", "error"]


# ---------- list_files ----------
class ListFilesInput(BaseModel):
    path: str = "."
    glob: Optional[str] = None
    max_entries: int = 500


class FileEntry(BaseModel):
    path: str
    is_dir: bool
    size: int = 0


class ListFilesOutput(BaseModel):
    entries: list[FileEntry] = Field(default_factory=list)
    truncated: bool = False


# ---------- read_file ----------
class ReadFileInput(BaseModel):
    path: str
    max_bytes: int = 64_000


class ReadFileOutput(BaseModel):
    path: str
    content: str
    truncated: bool = False
    bytes: int = 0


# ---------- rg_search ----------
class RgSearchInput(BaseModel):
    query: str = Field(min_length=1)
    path: str = "."
    glob: Optional[str] = "*.py"
    case_sensitive: bool = True
    max_matches: int = Field(default=50, ge=1, le=500)
    max_file_bytes: int = Field(default=256_000, ge=1)


class RgSearchMatch(BaseModel):
    source_path: str
    line_number: int = Field(ge=1)
    line_range: dict[str, int]
    line_text: str
    content_hash: str
    trace_id: str
    confidence: float = Field(ge=0, le=1)
    retrieval_source: Literal["rg"] = "rg"
    engine: Literal["rg", "python_fallback"]


class RgSearchOutput(BaseModel):
    query: str
    matches: list[RgSearchMatch] = Field(default_factory=list)
    truncated: bool = False
    engine: Literal["rg", "python_fallback"]
    warnings: list[str] = Field(default_factory=list)


# ---------- analyze_project ----------
class AnalyzeProjectInput(BaseModel):
    snapshot_id: Optional[str] = None
    target_scope: list[str] = Field(default_factory=list)


class FileInfo(BaseModel):
    path: str
    kind: Literal["source", "test", "config", "other"] = "source"
    size: int = 0


class RouteInfo(BaseModel):
    method: str
    path: str
    handler: str
    file: str


class FunctionInfo(BaseModel):
    name: str
    signature: str
    file: str


class ModelFieldInfo(BaseModel):
    name: str
    type: str = "Any"
    required: bool = True
    default: Optional[str] = None


class ModelInfo(BaseModel):
    name: str
    file: str
    fields: list[ModelFieldInfo] = Field(default_factory=list)


class TestFunctionInfo(BaseModel):
    name: str
    line: int
    # 静态 AST 估算，只能当提示；真实 nodeid 以 run_pytest 收集为准
    estimated_nodeid: str


class FixtureInfo(BaseModel):
    name: str
    file: str
    line: int
    dependencies: list[str] = Field(default_factory=list)


class ExistingTestInfo(BaseModel):
    path: str
    test_functions: list[TestFunctionInfo] = Field(default_factory=list)


class AnalyzeProjectOutput(BaseModel):
    files: list[FileInfo] = Field(default_factory=list)
    routes: list[RouteInfo] = Field(default_factory=list)
    functions: list[FunctionInfo] = Field(default_factory=list)
    models: list[ModelInfo] = Field(default_factory=list)
    fixtures: list[FixtureInfo] = Field(default_factory=list)
    existing_tests: list[ExistingTestInfo] = Field(default_factory=list)
    dependency_files: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ---------- write_test_file ----------
class WriteTestFileInput(BaseModel):
    attempt_id: Optional[str] = None
    path: str
    content: str
    reason: Optional[str] = None


class WriteTestFileOutput(BaseModel):
    file_id: str
    path: str
    content_hash: str
    bytes: int


# ---------- run_pytest ----------
class RunPytestInput(BaseModel):
    attempt_id: Optional[str] = None
    test_paths: list[str] = Field(default_factory=list)
    timeout_seconds: int = 120


class CaseResult(BaseModel):
    nodeid: str
    status: CaseStatus
    duration_ms: int = 0
    message: Optional[str] = None
    failure_type: Optional[FailureType] = None
    # 保留原始判据，便于审计/重算分类——别把信息丢了再祈祷分类永远对
    when_failed: Optional[str] = None  # setup / call / teardown
    exc_type: Optional[str] = None  # 真实异常类型名，如 AssertionError


class FailureDetail(BaseModel):
    nodeid: str
    file: Optional[str] = None
    line: Optional[int] = None
    exc_type: Optional[str] = None
    message: Optional[str] = None
    traceback: Optional[str] = None
    failure_type: Optional[FailureType] = None


class ArtifactRef(BaseModel):
    type: str
    uri: str


class RunPytestOutput(BaseModel):
    exit_code: int
    collected: int = 0  # pytest 真实收集到的测试项数（session.items），不含收集失败
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    duration_ms: int = 0
    case_results: list[CaseResult] = Field(default_factory=list)
    failures: list[FailureDetail] = Field(default_factory=list)
    # 收集阶段失败独立成类，绝不混进 collected / case_results，否则收集成功率等指标会脏
    collection_errors: list[FailureDetail] = Field(default_factory=list)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    # 系统级错误（超时/pytest 没起来）填这里；业务失败填 failures，收集失败填 collection_errors
    error: Optional[str] = None
