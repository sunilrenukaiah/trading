"""IDE-specific project configuration (Cursor / PyCharm Community)."""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.dom import minidom

from platform_utils import (
    BACKEND,
    IS_WINDOWS,
    ROOT,
    VENV_DIR,
    PYTHON,
    print_header,
    print_step,
    venv_python,
)

IDE_CHOICES = ("cursor", "pycharm")


def normalize_ide(ide: str | None) -> str:
    value = (ide or "cursor").strip().lower()
    if value not in IDE_CHOICES:
        raise ValueError(f"IDE must be one of: {', '.join(IDE_CHOICES)}")
    return value


def _posix_path(path: Path) -> str:
    return path.as_posix()


def _interpreter_path() -> Path:
    return venv_python()


def check_ide_compatibility(ide: str) -> tuple[bool, list[tuple[bool, str, str]]]:
    """Verify venv/interpreter is usable by the target IDE."""
    ide = normalize_ide(ide)
    results: list[tuple[bool, str, str]] = []

    py = _interpreter_path()
    ok = py.exists()
    results.append((ok, "Project virtualenv interpreter", str(py)))
    if not ok:
        return False, results

    import_ok = False
    detail = ""
    if py.exists():
        import subprocess

        proc = subprocess.run(
            [str(py), "-c", "import streamlit, asyncpg, app.config"],
            cwd=BACKEND,
            capture_output=True,
            text=True,
        )
        import_ok = proc.returncode == 0
        detail = "streamlit, asyncpg, app.config" if import_ok else (proc.stderr or proc.stdout or "import failed")[:200]
    results.append((import_ok, "Backend imports from venv", detail))

    if ide == "pycharm":
        # PyCharm Community on Windows uses the same venv; optional sanity check
        streamlit = VENV_DIR / ("Scripts/streamlit.exe" if IS_WINDOWS else "bin/streamlit")
        results.append(
            (
                streamlit.exists() or import_ok,
                "Streamlit launcher in venv",
                str(streamlit) if streamlit.exists() else "Use module run (configured in run configuration)",
            )
        )

    return all(r[0] for r in results), results


def setup_cursor() -> tuple[bool, str]:
    vscode_dir = ROOT / ".vscode"
    vscode_dir.mkdir(exist_ok=True)

    settings_path = vscode_dir / "settings.json"
    settings: dict = {}
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            settings = {}

    py_path = _interpreter_path()
    settings.update(
        {
            "python.defaultInterpreterPath": _posix_path(py_path),
            "python.terminal.activateEnvironment": True,
            "python.analysis.extraPaths": ["backend"],
            "terminal.integrated.cwd": _posix_path(BACKEND),
            "files.exclude": {
                "**/__pycache__": True,
                "**/.pytest_cache": True,
            },
        }
    )
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")

    launch_path = vscode_dir / "launch.json"
    launch = {
        "version": "0.2.0",
        "configurations": [
            {
                "name": "Streamlit Dashboard (with health check)",
                "type": "debugpy",
                "request": "launch",
                "program": _posix_path(ROOT / "scripts" / "run_app.py"),
                "cwd": _posix_path(ROOT),
                "python": _posix_path(py_path),
                "console": "integratedTerminal",
            },
            {
                "name": "FastAPI (optional)",
                "type": "debugpy",
                "request": "launch",
                "module": "uvicorn",
                "args": ["app.main:app", "--reload", "--port", "8000"],
                "cwd": _posix_path(BACKEND),
                "python": _posix_path(py_path),
                "console": "integratedTerminal",
            },
        ],
    }
    launch_path.write_text(json.dumps(launch, indent=2) + "\n", encoding="utf-8")

    return True, f"Wrote {settings_path.name} and {launch_path.name} for Cursor / VS Code"


def _pycharm_run_configuration(
    name: str,
    *,
    script: Path,
    working_dir: Path,
    parameters: str = "",
) -> str:
    root = ET.Element("component", {"name": "ProjectRunConfigurationManager"})
    cfg = ET.SubElement(
        root,
        "configuration",
        {
            "default": "false",
            "name": name,
            "type": "PythonConfigurationType",
            "factoryName": "Python",
        },
    )
    ET.SubElement(cfg, "module", {"name": "trading"})
    ET.SubElement(cfg, "option", {"name": "ENV_FILES", "value": ""})
    ET.SubElement(cfg, "option", {"name": "INTERPRETER_OPTIONS", "value": ""})
    ET.SubElement(cfg, "option", {"name": "PARENT_ENVS", "value": "true"})
    envs = ET.SubElement(cfg, "envs")
    ET.SubElement(envs, "env", {"name": "PYTHONUNBUFFERED", "value": "1"})
    ET.SubElement(cfg, "option", {"name": "SDK_HOME", "value": ""})
    ET.SubElement(cfg, "option", {"name": "WORKING_DIRECTORY", "value": f"$PROJECT_DIR$/{working_dir.relative_to(ROOT).as_posix()}"})
    ET.SubElement(cfg, "option", {"name": "IS_MODULE_SDK", "value": "true"})
    ET.SubElement(cfg, "option", {"name": "ADD_CONTENT_ROOTS", "value": "true"})
    ET.SubElement(cfg, "option", {"name": "ADD_SOURCE_ROOTS", "value": "true"})
    ET.SubElement(cfg, "option", {"name": "SCRIPT_NAME", "value": f"$PROJECT_DIR$/{script.relative_to(ROOT).as_posix()}"})
    ET.SubElement(cfg, "option", {"name": "PARAMETERS", "value": parameters})
    ET.SubElement(cfg, "option", {"name": "SHOW_COMMAND_LINE", "value": "false"})
    ET.SubElement(cfg, "option", {"name": "EMULATE_TERMINAL", "value": "true"})
    ET.SubElement(cfg, "option", {"name": "MODULE_MODE", "value": "false"})
    ET.SubElement(cfg, "option", {"name": "REDIRECT_INPUT", "value": "false"})
    ET.SubElement(cfg, "option", {"name": "INPUT_FILE", "value": ""})
    ET.SubElement(cfg, "method", {"v": "2"})
    rough = ET.tostring(root, encoding="unicode")
    parsed = minidom.parseString(rough)
    return parsed.toprettyxml(indent="  ")


def setup_pycharm() -> tuple[bool, str]:
    idea_dir = ROOT / ".idea"
    run_dir = idea_dir / "runConfigurations"
    run_dir.mkdir(parents=True, exist_ok=True)

    configs = {
        "Streamlit_Dashboard.xml": _pycharm_run_configuration(
            "Streamlit Dashboard",
            script=ROOT / "scripts" / "run_app.py",
            working_dir=ROOT,
        ),
        "Startup_Health_Check.xml": _pycharm_run_configuration(
            "Startup Health Check",
            script=ROOT / "scripts" / "startup_checklist.py",
            working_dir=ROOT,
        ),
        "FastAPI_Server.xml": _pycharm_run_configuration(
            "FastAPI Server",
            script=ROOT / "scripts" / "pycharm_run_fastapi.py",
            working_dir=ROOT,
        ),
    }
    for filename, content in configs.items():
        (run_dir / filename).write_text(content, encoding="utf-8")

    misc_path = idea_dir / "misc.xml"
    if not misc_path.exists():
        misc_path.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<project version="4">
  <component name="ProjectRootManager" version="2" project-jdk-name="Python 3.11 (backend .venv)" project-jdk-type="Python SDK" />
</project>
""",
            encoding="utf-8",
        )

    modules_path = idea_dir / "modules.xml"
    if not modules_path.exists():
        iml_name = "trading.iml"
        modules_path.write_text(
            f"""<?xml version="1.0" encoding="UTF-8"?>
<project version="4">
  <component name="ProjectModuleManager">
    <modules>
      <module fileurl="file://$PROJECT_DIR$/.idea/{iml_name}" filepath="$PROJECT_DIR$/.idea/{iml_name}" />
    </modules>
  </component>
</project>
""",
            encoding="utf-8",
        )

    iml_path = idea_dir / "trading.iml"
    if not iml_path.exists():
        iml_path.write_text(
            f"""<?xml version="1.0" encoding="UTF-8"?>
<module type="PYTHON_MODULE" version="4">
  <component name="NewModuleRootManager" inherit-compiler-output="true">
    <content url="file://$MODULE_DIR$/../backend">
      <sourceFolder url="file://$MODULE_DIR$/../backend" isTestSource="false" />
    </content>
    <orderEntry type="jdk" jdkName="Python 3.11 (backend .venv)" jdkType="Python SDK" />
    <orderEntry type="sourceFolder" forTests="false" />
  </component>
</module>
""",
            encoding="utf-8",
        )

    py = _interpreter_path()
    readme = ROOT / "docs" / "PYCHARM.md"
    return True, (
        f"Wrote PyCharm run configurations under .idea/runConfigurations/\n"
        f"         Set interpreter to: {py}\n"
        f"         See {readme.relative_to(ROOT)} for Community Edition steps"
    )


def configure_ide(ide: str) -> tuple[bool, str]:
    ide = normalize_ide(ide)
    if ide == "cursor":
        return setup_cursor()
    return setup_pycharm()


def ide_next_steps(ide: str) -> str:
    ide = normalize_ide(ide)
    if ide == "pycharm":
        py = _interpreter_path()
        win_py = str(py).replace("/", "\\") if IS_WINDOWS else str(py)
        return "\n".join(
            [
                "PyCharm Community Edition — next steps:",
                f"  1. Open folder: {ROOT}",
                f"  2. File → Settings → Project → Python Interpreter",
                f"  3. Add → Existing → {win_py}",
                "  4. Mark backend/ as Sources Root (right-click → Mark Directory as → Sources Root)",
                "  5. Run ▶ configuration: Streamlit Dashboard",
                "     (runs startup health check, then http://localhost:8501)",
                "",
                "  Docs: docs/PYCHARM.md",
            ]
        )
    return "\n".join(
        [
            "Cursor — next steps:",
            f"  1. Open folder: {ROOT}",
            "  2. Python interpreter should auto-select backend/.venv (see .vscode/settings.json)",
            "  3. Terminal: python scripts/run_app.py",
            "  4. Or use Run and Debug → Streamlit Dashboard",
            "",
            "  Docs: docs/MIGRATION.md",
        ]
    )


def run_ide_setup(ide: str) -> int:
    ide = normalize_ide(ide)
    print_header(f"IDE setup — {ide}")

    ok, checks = check_ide_compatibility(ide)
    for step_ok, label, detail in checks:
        print_step(step_ok, label, detail=detail)
    if not ok:
        print("  Fix venv/packages first (re-run Setup.py without skipping pip install).")
        return 1

    configured, msg = configure_ide(ide)
    print_step(configured, f"Configure {ide}", detail=msg)
    print()
    print(ide_next_steps(ide))
    return 0 if configured else 1


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "cursor"
    raise SystemExit(run_ide_setup(target))
