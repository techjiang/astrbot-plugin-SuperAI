"""CI 依赖完整性测试。

流水线镜像里没有 AstrBot，插件运行依赖必须由 CI 显式安装。
这里把「运行依赖已写入 requirements.txt」和「测试依赖已声明」固化下来，
避免再次出现 ModuleNotFoundError 导致收集阶段整体失败。
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# superai 直接 import 的三方运行依赖（AstrBot 内置，但测试环境需要自带）
RUNTIME_DEPS = ["aiohttp"]


def _requirement_names(path: Path) -> set[str]:
    """解析 requirements 文件中的包名（小写，去掉版本约束与注释）。"""
    names: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        names.add(re.split(r"[<>=!~\[; ]", line, maxsplit=1)[0].strip().lower())
    return names


@pytest.fixture(scope="module")
def runtime_requirements() -> set[str]:
    return _requirement_names(ROOT / "requirements.txt")


@pytest.fixture(scope="module")
def dev_requirements() -> set[str]:
    return _requirement_names(ROOT / "requirements-dev.txt")


@pytest.mark.parametrize("dep", RUNTIME_DEPS)
def test_runtime_dep_declared(dep, runtime_requirements):
    assert dep in runtime_requirements, f"{dep} 必须声明在 requirements.txt 中"


def test_all_runtime_imports_are_declared(runtime_requirements):
    """superai 中 import 的每个三方包都必须在 requirements.txt 里声明。"""
    stdlib = set(__import__("sys").stdlib_module_names)
    third_party: set[str] = set()
    for py_file in (ROOT / "superai").rglob("*.py"):
        for line in py_file.read_text(encoding="utf-8").splitlines():
            match = re.match(r"\s*(?:import|from)\s+([A-Za-z_][\w]*)", line)
            if match:
                third_party.add(match.group(1).lower())
    third_party -= stdlib
    third_party -= {"astrbot", "superai", "tests"}
    # 只校验仓库自身声明的运行依赖是否覆盖导入（astrbot 由宿主提供）
    assert third_party <= runtime_requirements, (
        f"以下三方包被 superai 导入但未声明在 requirements.txt：{sorted(third_party - runtime_requirements)}"
    )


def test_ci_installs_requirements_files():
    """流水线必须安装运行依赖与测试依赖，否则测试环境会缺包。"""
    cnb_yml = (ROOT / ".cnb.yml").read_text(encoding="utf-8")
    assert "requirements.txt" in cnb_yml
    assert "requirements-dev.txt" in cnb_yml


def test_dev_requirements_declared():
    dev = _requirement_names(ROOT / "requirements-dev.txt")
    assert {"ruff", "pytest", "pytest-asyncio"} <= dev


def test_test_suite_imports_are_declared(dev_requirements):
    """测试代码 import 的三方包必须声明在 requirements-dev.txt 里。"""
    stdlib = set(__import__("sys").stdlib_module_names)
    third_party: set[str] = set()
    for py_file in (ROOT / "tests").rglob("*.py"):
        if "stubs" in py_file.parts:
            continue
        for line in py_file.read_text(encoding="utf-8").splitlines():
            match = re.match(r"\s*(?:import|from)\s+([A-Za-z_][\w]*)", line)
            if match:
                third_party.add(match.group(1).lower())
    third_party -= stdlib
    third_party -= {"astrbot", "superai", "tests", "conftest"}
    # 包名与 import 名不一致的映射（import yaml -> pyyaml）
    aliases = {"yaml": "pyyaml"}
    third_party = {aliases.get(name, name) for name in third_party}
    assert third_party <= dev_requirements, (
        "以下三方包被 tests 导入但未声明在 requirements-dev.txt："
        f"{sorted(third_party - dev_requirements)}"
    )
