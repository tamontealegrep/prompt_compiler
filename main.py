#!/usr/bin/env python3
"""TUI interactivo para el compilador de prompts.

Uso:
    python main.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import webbrowser
from pathlib import Path

from app import mermaid_diagrams


# ---------------------------------------------------------------------------
# Rich opcional
# ---------------------------------------------------------------------------

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Prompt
    from rich.table import Table

    console = Console()
    _RICH = True

except ImportError:
    console = None  # type: ignore[assignment]
    _RICH = False


ROOT = Path(__file__).resolve().parent
CONFIGS_DIR = ROOT / "configs"
DIST_DIR = ROOT / "dist"
PROFILES_DIR = ROOT / "profiles"


# ---------------------------------------------------------------------------
# Helpers de UI
# ---------------------------------------------------------------------------

def _cprint(rich_text: str, plain_text: str | None = None) -> None:
    if _RICH:
        console.print(rich_text)
    else:
        print(plain_text if plain_text is not None else re.sub(r"\[.*?\]", "", rich_text))


def _title(text: str) -> None:
    if _RICH:
        console.print(f"\n[bold cyan]{text}[/bold cyan]")
        console.print("─" * min(len(text), 60), style="dim")
    else:
        print(f"\n{text}")
        print("─" * min(len(text), 60))


def _ok(text: str) -> None:
    _cprint(f"[bold green]✓[/bold green] {text}", f"✓ {text}")


def _err(text: str) -> None:
    _cprint(f"[bold red]✗[/bold red] {text}", f"✗ {text}")


def _info(text: str) -> None:
    _cprint(f"  [dim]{text}[/dim]", f"  {text}")


def _ask(prompt_text: str, default: str = "") -> str:
    if _RICH:
        return Prompt.ask(prompt_text, default=default) or default

    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt_text}{suffix}: ").strip()

    return value if value else default


def _ask_yn(prompt_text: str, default: bool = True) -> bool:
    hint = "S/n" if default else "s/N"

    while True:
        raw = _ask(
            f"{prompt_text} ({hint})",
            "s" if default else "n",
        ).lower()

        if raw in ("s", "si", "sí", "y", "yes", "1"):
            return True

        if raw in ("n", "no", "0"):
            return False

        print("  Responde s o n.")


def _select(
    items: list[str],
    title: str | None = None,
    back_label: str = "← Volver",
) -> str | None:
    """
    Muestra una lista numerada.

    Retorna:
        - El elemento elegido.
        - None si elige 0.
    """
    if not items:
        _info("No hay elementos disponibles.")
        return None

    if title:
        _title(title)

    if _RICH:
        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column("n", style="bold yellow", width=4, no_wrap=True)
        table.add_column("item", style="cyan")

        for index, item in enumerate(items, 1):
            table.add_row(f"[{index}]", item)

        table.add_row("[0]", f"[dim]{back_label}[/dim]")

        console.print(table)

    else:
        for index, item in enumerate(items, 1):
            print(f"  [{index}] {item}")

        print(f"  [0] {back_label}")

    raw = _ask("Selección", "0")

    try:
        index = int(raw)

        if index == 0:
            return None

        if 1 <= index <= len(items):
            return items[index - 1]

    except ValueError:
        pass

    _err("Opción inválida.")

    return None


def _pause() -> None:
    input("\nPresiona Enter para continuar...")


def _clear() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# Descubrimiento de configs / perfiles
# ---------------------------------------------------------------------------

def _list_configs() -> list[str]:
    if not CONFIGS_DIR.exists():
        return []

    return sorted(
        directory.name
        for directory in CONFIGS_DIR.iterdir()
        if directory.is_dir()
    )


def _list_compiled() -> list[str]:
    if not DIST_DIR.exists():
        return []

    return sorted(
        directory.name
        for directory in DIST_DIR.iterdir()
        if directory.is_dir()
    )


def _list_compliance_profiles() -> list[str]:
    compliance_dir = PROFILES_DIR / "compliance"

    if not compliance_dir.exists():
        return []

    return [
        path.stem
        for path in sorted(compliance_dir.glob("*.yaml"))
    ]


def _list_channels() -> list[str]:
    channels_dir = PROFILES_DIR / "channels"

    if not channels_dir.exists():
        return ["voice", "chat", "async_text"]

    channels = [
        path.stem
        for path in sorted(channels_dir.glob("*.yaml"))
    ]

    return channels or ["voice", "chat", "async_text"]


# ---------------------------------------------------------------------------
# Opciones de compilación
# ---------------------------------------------------------------------------

_VERBOSITY_OPTIONS = ["minimal", "standard", "verbose"]

_DEFAULT_OPTS: dict = {
    "channel": "voice",
    "verbosity": "standard",
    "reference_formats": ["markdown", "json"],
    "compliance_profile": None,
    "fail_on_warnings": False,
    "no_reference_asset": False,
}


def _configure_opts(current: dict | None = None) -> dict:
    """
    Wizard paso a paso para configurar opciones de compilación.
    """
    opts = dict(current or _DEFAULT_OPTS)

    _title("Opciones de compilación")
    _info("Presiona Enter para mantener el valor actual.\n")

    channels = _list_channels()

    _info(f"Canal actual: {opts['channel']}")

    if _ask_yn("¿Cambiar canal?", default=False):
        channel = _select(
            channels,
            title="Canal",
            back_label="(mantener actual)",
        )

        if channel:
            opts["channel"] = channel

    _info(f"Verbosity actual: {opts['verbosity']}")

    if _ask_yn("¿Cambiar verbosity?", default=False):
        verbosity = _select(
            _VERBOSITY_OPTIONS,
            title="Verbosity",
            back_label="(mantener actual)",
        )

        if verbosity:
            opts["verbosity"] = verbosity

    opts["no_reference_asset"] = _ask_yn(
        "¿Omitir Reference Asset?",
        default=opts.get("no_reference_asset", False),
    )

    if not opts["no_reference_asset"]:
        _info(f"Formatos actuales: {', '.join(opts['reference_formats'])}")

        if _ask_yn("¿Cambiar formatos de referencia?", default=False):
            selected_format = _select(
                ["markdown", "json", "markdown y json"],
                title="Formatos",
                back_label="(mantener actual)",
            )

            if selected_format:
                if "y" in selected_format:
                    opts["reference_formats"] = ["markdown", "json"]
                elif "markdown" in selected_format:
                    opts["reference_formats"] = ["markdown"]
                else:
                    opts["reference_formats"] = ["json"]

    profiles = _list_compliance_profiles()

    if profiles:
        current_profile = opts.get("compliance_profile") or "ninguno"

        _info(f"Compliance profile actual: {current_profile}")

        if _ask_yn("¿Cambiar compliance profile?", default=False):
            options = ["ninguno"] + profiles

            selected_profile = _select(
                options,
                title="Compliance Profile",
                back_label="(mantener actual)",
            )

            if selected_profile:
                opts["compliance_profile"] = (
                    None if selected_profile == "ninguno" else selected_profile
                )

    opts["fail_on_warnings"] = _ask_yn(
        "¿Tratar warnings como errores?",
        default=opts.get("fail_on_warnings", False),
    )

    return opts


def _build_cmd(config_name: str, opts: dict) -> list[str]:
    cmd = [
        sys.executable,
        "build_prompt.py",
        f"configs/{config_name}",
    ]

    cmd += ["--channel", opts["channel"]]
    cmd += ["--verbosity", opts["verbosity"]]

    if opts.get("no_reference_asset"):
        cmd += ["--no-reference-asset"]

    elif opts.get("reference_formats"):
        cmd += ["--reference-formats"] + opts["reference_formats"]

    if opts.get("compliance_profile"):
        cmd += ["--compliance-profile", opts["compliance_profile"]]

    if opts.get("fail_on_warnings"):
        cmd += ["--fail-on-warnings"]

    return cmd


# ---------------------------------------------------------------------------
# Ejecución de compilación
# ---------------------------------------------------------------------------

def _show_dist_files(config_name: str) -> None:
    dist_path = DIST_DIR / config_name

    if not dist_path.exists():
        return

    # Collect root-level files and subdirectory entries separately.
    root_files = sorted(p for p in dist_path.iterdir() if p.is_file())
    subdirs = sorted(p for p in dist_path.iterdir() if p.is_dir())

    if not root_files and not subdirs:
        return

    if _RICH:
        table = Table(
            title=f"\nArchivos en dist/{config_name}/",
            box=None,
            padding=(0, 2),
            show_header=True,
            header_style="bold",
        )

        table.add_column("Ruta", style="cyan")
        table.add_column("Tamaño", style="dim", justify="right")

        for file_path in root_files:
            size = file_path.stat().st_size
            size_text = f"{size / 1024:.1f} KB" if size >= 1024 else f"{size} B"
            table.add_row(file_path.name, size_text)

        for subdir in subdirs:
            sub_files = sorted(subdir.iterdir())
            for file_path in sub_files:
                if file_path.is_file():
                    size = file_path.stat().st_size
                    size_text = (
                        f"{size / 1024:.1f} KB" if size >= 1024 else f"{size} B"
                    )
                    table.add_row(f"{subdir.name}/{file_path.name}", size_text)

        console.print(table)

    else:
        print(f"\nArchivos en dist/{config_name}/:")

        for file_path in root_files:
            size = file_path.stat().st_size
            size_text = f"{size / 1024:.1f} KB" if size >= 1024 else f"{size} B"
            print(f"  {file_path.name:<42} {size_text:>10}")

        for subdir in subdirs:
            sub_files = sorted(subdir.iterdir())
            for file_path in sub_files:
                if file_path.is_file():
                    size = file_path.stat().st_size
                    size_text = (
                        f"{size / 1024:.1f} KB" if size >= 1024 else f"{size} B"
                    )
                    rel = f"{subdir.name}/{file_path.name}"
                    print(f"  {rel:<42} {size_text:>10}")


def _run_compilation(config_name: str, opts: dict) -> bool:
    """
    Ejecuta build_prompt.py.

    Returns:
        True si compiló correctamente.
    """
    cmd = _build_cmd(config_name, opts)

    if _RICH:
        console.print(f"\n[bold]Ejecutando:[/bold] [dim]{' '.join(cmd)}[/dim]\n")
    else:
        print(f"\nEjecutando: {' '.join(cmd)}\n")

    print("─" * 60)

    result = subprocess.run(cmd, cwd=str(ROOT))

    print("─" * 60)

    if result.returncode == 0:
        _ok(f"Compilación exitosa: {config_name}")
        _show_dist_files(config_name)

        return True

    _err(f"Compilación fallida, código de salida: {result.returncode}")

    return False


# ---------------------------------------------------------------------------
# Flujo interactivo de generación de diagramas
# ---------------------------------------------------------------------------

def _diagram_flow(config_name: str, channel: str = "voice") -> Path | None:
    """
    Wizard completo para generar y exportar un diagrama Mermaid.
    """
    _title(f"Diagrama de estados — {config_name}")

    _info("Cargando configuración...")

    try:
        spec = mermaid_diagrams.load_spec(config_name, channel)

    except Exception as exc:
        _err(f"No se pudo cargar la configuración: {exc}")
        return None

    all_states = mermaid_diagrams.get_all_states(spec)
    subflow_prefixes = mermaid_diagrams.get_subflow_prefixes(spec)

    _info(
        f"Estados encontrados: {len(all_states)} "
        f"({len(subflow_prefixes)} subflows)\n"
    )

    mode_options = [
        "Completo — todos los estados con subgraphs",
        "Solo raíz — estados sin subflow",
    ]

    if subflow_prefixes:
        mode_options.append("Subflow específico")

    choice = _select(mode_options, title="Tipo de diagrama")

    if choice is None:
        return None

    mode = "full"
    subflow_filter = None

    if "raíz" in choice:
        mode = "root_only"

    elif "Subflow" in choice:
        mode = "subflow"

        subflow_filter = _select(
            subflow_prefixes,
            title="Selecciona subflow",
        )

        if subflow_filter is None:
            return None

    _info("Generando código Mermaid...")

    try:
        mermaid_code = mermaid_diagrams.generate_mermaid(
            spec,
            mode=mode,
            subflow_filter=subflow_filter,
        )

    except Exception as exc:
        _err(f"Error generando Mermaid: {exc}")
        return None

    suffix = ""

    if subflow_filter:
        suffix = f"_{subflow_filter}"

    elif mode != "full":
        suffix = f"_{mode}"

    output_dir = DIST_DIR / config_name / "diagrams"

    _info("Exportando diagrama: Mermaid CLI → mermaid.ink → HTML fallback...")

    try:
        result = mermaid_diagrams.export_diagram(
            mermaid_code,
            output_dir,
            suffix,
            config_name=config_name,
            subflow_filter=subflow_filter,
        )

    except Exception as exc:
        _err(f"No se pudo exportar el diagrama: {exc}")
        return None

    _ok(f"Código Mermaid: {_relative(result.mmd_path)}")

    if result.output_path.suffix.lower() == ".png":
        if result.method == "mmdc":
            _ok(f"Imagen PNG generada con Mermaid CLI: {_relative(result.output_path)}")
        else:
            _ok(f"Imagen PNG generada con mermaid.ink: {_relative(result.output_path)}")

    else:
        _ok(f"HTML generado: {_relative(result.output_path)}")

    if _ask_yn(f"\n¿Abrir {result.output_path.name}?", default=True):
        webbrowser.open(result.output_path.as_uri())

    return result.output_path


# ---------------------------------------------------------------------------
# Flujos del menú principal
# ---------------------------------------------------------------------------

def _flow_compile() -> None:
    configs = _list_configs()

    if not configs:
        _err(f"No se encontraron configuraciones en {CONFIGS_DIR}/")
        return

    config = _select(configs, title="Selecciona configuración")

    if config is None:
        return

    use_defaults = _ask_yn(
        "¿Usar opciones por defecto (voice, standard)?",
        default=True,
    )

    opts = _DEFAULT_OPTS.copy()

    if not use_defaults:
        opts = _configure_opts(opts)

    success = _run_compilation(config, opts)

    if success and _ask_yn("\n¿Generar diagrama Mermaid?", default=True):
        _diagram_flow(config, channel=opts["channel"])


def _flow_diagram() -> None:
    configs = _list_configs()

    if not configs:
        _err(f"No se encontraron configuraciones en {CONFIGS_DIR}/")
        return

    config = _select(configs, title="Selecciona configuración")

    if config is None:
        return

    channels = _list_channels()

    _info("Canal por defecto: voice")

    channel = "voice"

    if _ask_yn("¿Cambiar canal?", default=False):
        selected_channel = _select(
            channels,
            title="Canal",
            back_label="(voice)",
        )

        if selected_channel:
            channel = selected_channel

    _diagram_flow(config, channel=channel)


def _flow_compile_and_diagram() -> None:
    configs = _list_configs()

    if not configs:
        _err(f"No se encontraron configuraciones en {CONFIGS_DIR}/")
        return

    config = _select(configs, title="Selecciona configuración")

    if config is None:
        return

    use_defaults = _ask_yn(
        "¿Usar opciones por defecto (voice, standard)?",
        default=True,
    )

    opts = _DEFAULT_OPTS.copy()

    if not use_defaults:
        opts = _configure_opts(opts)

    success = _run_compilation(config, opts)

    if success:
        _info("Generando diagrama completo (todos los estados)...")
        try:
            spec = mermaid_diagrams.load_spec(config, opts["channel"])
            mermaid_code = mermaid_diagrams.generate_mermaid(spec, mode="full")
            output_dir = DIST_DIR / config / "diagrams"
            result = mermaid_diagrams.export_diagram(mermaid_code, output_dir, config_name=config)
            _ok(f"Diagrama generado: {_relative(result.output_path)}")
            if _ask_yn(f"\n¿Abrir {result.output_path.name}?", default=True):
                webbrowser.open(result.output_path.as_uri())
        except Exception as exc:
            _err(f"No se pudo generar el diagrama: {exc}")


def _flow_view_dist() -> None:
    compiled = _list_compiled()

    if not compiled:
        _info("No hay compilaciones previas en dist/")
        return

    config = _select(compiled, title="Selecciona agente compilado")

    if config is None:
        return

    dist_path = DIST_DIR / config
    # Collect all files recursively, show relative path from dist_path.
    all_files = sorted(
        p for p in dist_path.rglob("*") if p.is_file()
    )

    if not all_files:
        _info("La carpeta está vacía.")
        return

    rel_names = [str(p.relative_to(dist_path)) for p in all_files]

    chosen = _select(rel_names, title="Selecciona archivo para abrir")

    if chosen:
        webbrowser.open((dist_path / chosen).as_uri())


# ---------------------------------------------------------------------------
# Menú principal
# ---------------------------------------------------------------------------

_MENU_ITEMS = [
    ("Compilar prompt", _flow_compile),
    ("Generar diagrama Mermaid", _flow_diagram),
    ("Compilar + Diagrama todo en uno", _flow_compile_and_diagram),
    ("Ver archivos de dist/", _flow_view_dist),
]


def _print_header() -> None:
    if _RICH:
        console.print(
            Panel.fit(
                "[bold cyan]Prompt Compiler[/bold cyan]  [dim]v1.0[/dim]\n"
                "[dim]Herramienta interactiva para compilar agentes conversacionales[/dim]",
                border_style="cyan",
                padding=(0, 4),
            )
        )

    else:
        print("\n╔════════════════════════════════════════╗")
        print("║      Prompt Compiler  v1.0             ║")
        print("║  Compilador de agentes conversacionales ║")
        print("╚════════════════════════════════════════╝")


def main() -> None:
    while True:
        _clear()
        _print_header()

        labels = [
            label
            for label, _ in _MENU_ITEMS
        ]

        choice = _select(labels, back_label="Salir")

        if choice is None:
            _cprint("\n[dim]¡Hasta luego![/dim]\n", "\n¡Hasta luego!\n")
            break

        for label, function in _MENU_ITEMS:
            if label == choice:
                function()
                break

        _pause()


if __name__ == "__main__":
    try:
        main()

    except KeyboardInterrupt:
        print("\n\nInterrumpido.")
        sys.exit(0)