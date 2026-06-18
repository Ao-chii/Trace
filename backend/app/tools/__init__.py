# 工具层对外入口：5 个工具 + 默认注册表。runtime/strategy 都从这里取工具。
from app.schemas.tools import (
    AnalyzeProjectInput,
    AnalyzeProjectOutput,
    ListFilesInput,
    ListFilesOutput,
    ReadFileInput,
    ReadFileOutput,
    RgSearchInput,
    RgSearchOutput,
    RunPytestInput,
    RunPytestOutput,
    WriteTestFileInput,
    WriteTestFileOutput,
)
from app.tools.analyze import analyze_project
from app.tools.base import ToolContext, ToolRegistry, ToolSpec
from app.tools.fs_tools import list_files, read_file
from app.tools.run_pytest import run_pytest
from app.tools.search import rg_search
from app.tools.write_test import write_test_file

__all__ = [
    "ToolContext",
    "ToolRegistry",
    "ToolSpec",
    "default_registry",
    "analyze_project",
    "list_files",
    "read_file",
    "rg_search",
    "run_pytest",
    "write_test_file",
]


def default_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(ToolSpec("list_files", ListFilesInput, ListFilesOutput, list_files))
    reg.register(ToolSpec("read_file", ReadFileInput, ReadFileOutput, read_file))
    reg.register(ToolSpec("rg_search", RgSearchInput, RgSearchOutput, rg_search))
    reg.register(ToolSpec("analyze_project", AnalyzeProjectInput, AnalyzeProjectOutput, analyze_project))
    reg.register(ToolSpec("write_test_file", WriteTestFileInput, WriteTestFileOutput, write_test_file))
    reg.register(ToolSpec("run_pytest", RunPytestInput, RunPytestOutput, run_pytest))
    return reg
