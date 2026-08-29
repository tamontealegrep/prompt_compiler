#!/usr/bin/env python3
"""Interactive TUI for the prompt compiler.

Usage:
    python main.py

Arrow-key menus via ``questionary`` when the terminal is interactive; a
plain numbered ``input()`` fallback is kept for non-TTY contexts (piped
stdin, CI, dumb terminals).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import webbrowser
from pathlib import Path

from app import mermaid_diagrams


# The plain-fallback path prints non-ASCII glyphs; keep them legible even when
# the console codepage is not UTF-8 (Windows cmd.exe, piped stdout).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass


# ---------------------------------------------------------------------------
# Optional dependencies
# ---------------------------------------------------------------------------

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    console = Console()
    _RICH = True

except ImportError:
    console = None  # type: ignore[assignment]
    _RICH = False

try:
    import questionary

    _STYLE = questionary.Style([
        ("qmark", "fg:#00d7af bold"),
        ("question", "bold"),
        ("answer", "fg:#00d7af bold"),
        ("pointer", "fg:#00d7af bold"),
        ("highlighted", "fg:#00d7af bold"),
        ("selected", "fg:#00d7af bold"),
        ("separator", "fg:#6c6c6c"),
        ("instruction", "fg:#6c6c6c italic"),
    ])

    _QUESTIONARY = True

except ImportError:
    questionary = None  # type: ignore[assignment]
    _STYLE = None
    _QUESTIONARY = False


# Arrow-key menus need both the library and a real interactive terminal.
_INTERACTIVE = _QUESTIONARY and sys.stdin.isatty() and sys.stdout.isatty()


ROOT = Path(__file__).resolve().parent
CONFIGS_DIR = ROOT / "agents" / "defs"
DIST_DIR = ROOT / "dist"
PROFILES_DIR = ROOT / "profiles"


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _cprint(rich_text: str, plain_text: str | None = None) -> None:
    if _RICH:
        console.print(rich_text)
    else:
        print(plain_text if plain_text is not None else re.sub(r"\[.*?\]", "", rich_text))


def _ok(text: str) -> None:
    _cprint(f"[bold green]✓[/bold green] {text}", f"✓ {text}")


def _err(text: str) -> None:
    _cprint(f"[bold red]✗[/bold red] {text}", f"✗ {text}")


def _info(text: str) -> None:
    _cprint(f"  [dim]{text}[/dim]", f"  {text}")


def _title(text: str) -> None:
    if _RICH:
        console.print(f"\n[bold cyan]{text}[/bold cyan]")
        console.print("─" * min(len(text), 60), style="dim")
    else:
        print(f"\n{text}")
        print("─" * min(len(text), 60))


def _clear() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def _screen(breadcrumb: str = "") -> None:
    """Clear the terminal and print a fixed header (plus an optional breadcrumb)."""
    _clear()

    if _RICH:
        console.print("[bold cyan]Prompt Compiler[/bold cyan] [dim]· TUI interactivo[/dim]")
        if breadcrumb:
            console.print(f"[dim]{breadcrumb}[/dim]")
        console.print()
    else:
        print("Prompt Compiler · TUI interactivo")
        if breadcrumb:
            print(breadcrumb)
        print()


def _pause() -> None:
    try:
        input("\nPresiona Enter para continuar...")
    except EOFError:
        pass


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# Input helpers — questionary when interactive, plain input() otherwise
# ---------------------------------------------------------------------------

_BACK = object()  # sentinel value for the "go back" menu entry


def _select(
    items: list[str] | list[tuple[str, object]],
    *,
    message: str = "Selección",
    breadcrumb: str = "",
    back_label: str = "← Volver",
    clear: bool = True,
):
    """
    Show a single-choice menu.

    Parameters:
        items: Either plain labels, or (label, value) pairs.
        message (str): Prompt shown above the choices.
        breadcrumb (str): Context line printed at the top of the screen.
        back_label (str): Label for the escape option.
        clear (bool): Redraw a fresh screen first. Set False to keep
            preceding output (e.g. a build log) visible above the menu.

    Returns:
        The chosen value (the label itself when items are plain strings),
        or None when the user picks the back option / aborts.
    """
    if not items:
        _info("No hay elementos disponibles.")
        return None

    paired = [it if isinstance(it, tuple) else (it, it) for it in items]

    if _INTERACTIVE:
        if clear:
            _screen(breadcrumb)
        choices = [
            questionary.Choice(title=label, value=value)
            for label, value in paired
        ]
        choices += [questionary.Separator(), questionary.Choice(title=back_label, value=_BACK)]
        answer = questionary.select(message, choices=choices, style=_STYLE).ask()

        return None if answer is None or answer is _BACK else answer

    if breadcrumb:
        print(breadcrumb)

    print(f"\n{message}:")
    for index, (label, _) in enumerate(paired, 1):
        print(f"  [{index}] {label}")
    print(f"  [0] {back_label}")

    raw = _text_plain("Selección", "0")

    try:
        index = int(raw)
        if index == 0:
            return None
        if 1 <= index <= len(paired):
            return paired[index - 1][1]
    except ValueError:
        pass

    _err("Opción inválida.")

    return None


def _checkbox(
    options: list[str],
    *,
    message: str,
    default: list[str],
    breadcrumb: str = "",
) -> list[str] | None:
    """
    Multi-choice menu. Returns the selected labels, or None on abort.
    An empty selection also returns None (caller keeps the current value).
    """
    if _INTERACTIVE:
        _screen(breadcrumb)
        choices = [
            questionary.Choice(title=opt, value=opt, checked=opt in default)
            for opt in options
        ]
        answer = questionary.checkbox(message, choices=choices, style=_STYLE).ask()

        return answer or None

    print(f"\n{message}")
    for index, opt in enumerate(options, 1):
        mark = "x" if opt in default else " "
        print(f"  [{index}] ({mark}) {opt}")

    raw = _text_plain("Números separados por espacio (Enter = mantener)", "")

    if not raw.strip():
        return None

    picked = []
    for token in raw.split():
        try:
            idx = int(token)
            if 1 <= idx <= len(options):
                picked.append(options[idx - 1])
        except ValueError:
            pass

    return picked or None


def _text(message: str, default: str = "") -> str:
    if _INTERACTIVE:
        answer = questionary.text(message, default=default, style=_STYLE).ask()
        return default if answer is None else answer

    return _text_plain(message, default)


def _text_plain(message: str, default: str) -> str:
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"{message}{suffix}: ").strip()
    except EOFError:
        return default

    return value or default


def _confirm(message: str, default: bool = True) -> bool:
    if _INTERACTIVE:
        answer = questionary.confirm(message, default=default, style=_STYLE).ask()
        return default if answer is None else answer

    hint = "S/n" if default else "s/N"
    while True:
        raw = _text_plain(f"{message} ({hint})", "s" if default else "n").lower()
        if raw in ("s", "si", "sí", "y", "yes", "1"):
            return True
        if raw in ("n", "no", "0"):
            return False
        print("  Responde s o n.")


# ---------------------------------------------------------------------------
# Discovery of configs / profiles
# ---------------------------------------------------------------------------

def _list_configs() -> list[str]:
    if not CONFIGS_DIR.exists():
        return []

    return sorted(d.name for d in CONFIGS_DIR.iterdir() if d.is_dir())


def _list_compiled() -> list[str]:
    if not DIST_DIR.exists():
        return []

    return sorted(d.name for d in DIST_DIR.iterdir() if d.is_dir())


def _list_compliance_profiles() -> list[str]:
    compliance_dir = PROFILES_DIR / "compliance"

    if not compliance_dir.exists():
        return []

    return [path.stem for path in sorted(compliance_dir.glob("*.yaml"))]


def _list_channels() -> list[str]:
    channels_dir = PROFILES_DIR / "channels"

    if not channels_dir.exists():
        return ["voice", "chat", "async_text"]

    channels = [path.stem for path in sorted(channels_dir.glob("*.yaml"))]

    return channels or ["voice", "chat", "async_text"]


# ---------------------------------------------------------------------------
# Compilation options
# ---------------------------------------------------------------------------

_VERBOSITY_OPTIONS = ["minimal", "standard", "verbose"]

_DEFAULT_OPTS: dict = {
    "channel": "voice",
    "verbosity": "standard",
    "reference_formats": ["markdown", "json"],
    "compliance_profile": None,
    "fail_on_warnings": False,
    "no_reference_asset": False,
    "embed_subflows": True,
}


def _opts_summary(opts: dict) -> str:
    parts = [f"canal={opts['channel']}", f"verbosity={opts['verbosity']}"]

    if opts.get("no_reference_asset"):
        parts.append("sin reference asset")
    else:
        parts.append(f"formatos={'+'.join(opts['reference_formats'])}")

    if opts.get("compliance_profile"):
        parts.append(f"compliance={opts['compliance_profile']}")
    if opts.get("fail_on_warnings"):
        parts.append("fail-on-warnings")
    if not opts.get("embed_subflows", True):
        parts.append("split-subflows")

    return ", ".join(parts)


def _configure_opts(current: dict, breadcrumb: str) -> dict:
    """Step-by-step wizard to configure compilation options."""
    opts = dict(current)

    channel = _select(
        _list_channels(),
        message="Canal",
        breadcrumb=f"{breadcrumb}  >  opciones  >  canal",
        back_label=f"(mantener: {opts['channel']})",
    )
    if channel:
        opts["channel"] = channel

    verbosity = _select(
        _VERBOSITY_OPTIONS,
        message="Verbosity",
        breadcrumb=f"{breadcrumb}  >  opciones  >  verbosity",
        back_label=f"(mantener: {opts['verbosity']})",
    )
    if verbosity:
        opts["verbosity"] = verbosity

    opts["no_reference_asset"] = _confirm(
        "¿Omitir el Reference Asset?",
        default=opts.get("no_reference_asset", False),
    )

    if not opts["no_reference_asset"]:
        formats = _checkbox(
            ["markdown", "json"],
            message="Formatos del Reference Asset",
            default=opts["reference_formats"],
            breadcrumb=f"{breadcrumb}  >  opciones  >  formatos",
        )
        if formats:
            opts["reference_formats"] = formats

    profiles = _list_compliance_profiles()
    if profiles:
        current_profile = opts.get("compliance_profile") or "ninguno"
        selected = _select(
            ["ninguno", *profiles],
            message="Compliance profile",
            breadcrumb=f"{breadcrumb}  >  opciones  >  compliance",
            back_label=f"(mantener: {current_profile})",
        )
        if selected:
            opts["compliance_profile"] = None if selected == "ninguno" else selected

    opts["fail_on_warnings"] = _confirm(
        "¿Tratar los warnings como errores?",
        default=opts.get("fail_on_warnings", False),
    )

    opts["embed_subflows"] = _confirm(
        "¿Embeber subflows en el system_prompt? (archivo único, sin carpeta subflows/)",
        default=opts.get("embed_subflows", True),
    )

    return opts


def _build_cmd(config_name: str, opts: dict) -> list[str]:
    cmd = [sys.executable, "app/build_prompt.py", f"agents/defs/{config_name}"]

    cmd += ["--channel", opts["channel"]]
    cmd += ["--verbosity", opts["verbosity"]]

    if opts.get("no_reference_asset"):
        cmd += ["--no-reference-asset"]
    elif opts.get("reference_formats"):
        cmd += ["--reference-formats", *opts["reference_formats"]]

    if opts.get("compliance_profile"):
        cmd += ["--compliance-profile", opts["compliance_profile"]]

    if opts.get("fail_on_warnings"):
        cmd += ["--fail-on-warnings"]

    if not opts.get("embed_subflows", True):
        cmd += ["--split-subflows"]

    return cmd


# ---------------------------------------------------------------------------
# Compilation execution
# ---------------------------------------------------------------------------

def _show_dist_files(config_name: str) -> None:
    dist_path = DIST_DIR / config_name

    if not dist_path.exists():
        return

    root_files = sorted(p for p in dist_path.iterdir() if p.is_file())
    subdirs = sorted(p for p in dist_path.iterdir() if p.is_dir())

    if not root_files and not subdirs:
        return

    def _size(path: Path) -> str:
        size = path.stat().st_size
        return f"{size / 1024:.1f} KB" if size >= 1024 else f"{size} B"

    rows: list[tuple[str, str]] = [(p.name, _size(p)) for p in root_files]
    for subdir in subdirs:
        for file_path in sorted(subdir.iterdir()):
            if file_path.is_file():
                rows.append((f"{subdir.name}/{file_path.name}", _size(file_path)))

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
        for name, size_text in rows:
            table.add_row(name, size_text)
        console.print(table)
    else:
        print(f"\nArchivos en dist/{config_name}/:")
        for name, size_text in rows:
            print(f"  {name:<42} {size_text:>10}")


def _run_compilation(config_name: str, opts: dict) -> bool:
    """
    Run app/build_prompt.py for one agent.

    Returns:
        bool: True when the build exits 0.
    """
    cmd = _build_cmd(config_name, opts)

    _cprint(f"\n[bold]Ejecutando:[/bold] [dim]{' '.join(cmd)}[/dim]\n", f"\nEjecutando: {' '.join(cmd)}\n")
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
# Mermaid diagram wizard
# ---------------------------------------------------------------------------

def _diagram_flow(config_name: str, channel: str, breadcrumb: str) -> Path | None:
    """Generate and export one Mermaid state diagram for an agent."""
    _screen(f"{breadcrumb}  >  diagrama")
    _title(f"Diagrama de estados — {config_name} ({channel})")

    _info("Cargando configuración...")

    try:
        spec = mermaid_diagrams.load_spec(config_name, channel)
    except Exception as exc:
        _err(f"No se pudo cargar la configuración: {exc}")
        return None

    all_states = mermaid_diagrams.get_all_states(spec)
    subflow_prefixes = mermaid_diagrams.get_subflow_prefixes(spec)

    _info(f"Estados encontrados: {len(all_states)} ({len(subflow_prefixes)} subflows)")

    mode_options: list[tuple[str, str]] = [
        ("Completo — todos los estados con subgraphs", "full"),
        ("Solo raíz — estados sin subflow", "root_only"),
    ]
    if subflow_prefixes:
        mode_options.append(("Subflow específico", "subflow"))

    mode = _select(mode_options, message="Tipo de diagrama", breadcrumb=f"{breadcrumb}  >  diagrama")
    if mode is None:
        return None

    subflow_filter = None
    if mode == "subflow":
        subflow_filter = _select(
            subflow_prefixes,
            message="Selecciona subflow",
            breadcrumb=f"{breadcrumb}  >  diagrama  >  subflow",
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
        tool = "Mermaid CLI" if result.method == "mmdc" else "mermaid.ink"
        _ok(f"Imagen PNG generada con {tool}: {_relative(result.output_path)}")
    else:
        _ok(f"HTML generado: {_relative(result.output_path)}")

    if _confirm(f"¿Abrir {result.output_path.name}?", default=True):
        webbrowser.open(result.output_path.as_uri())

    return result.output_path


# ---------------------------------------------------------------------------
# Main-menu flows
# ---------------------------------------------------------------------------

def _flow_compile() -> None:
    breadcrumb = "compilar"
    config = _select(_list_configs(), message="Selecciona configuración", breadcrumb=breadcrumb)
    if config is None:
        _err_if_no_configs()
        return

    breadcrumb = f"compilar  >  {config}"
    opts = _DEFAULT_OPTS.copy()

    if not _confirm(f"¿Usar opciones por defecto ({_opts_summary(opts)})?", default=True):
        opts = _configure_opts(opts, breadcrumb)

    while True:
        _screen(breadcrumb)
        _info(f"Opciones: {_opts_summary(opts)}")
        success = _run_compilation(config, opts)

        actions: list[tuple[str, str]] = []
        if success:
            actions.append(("Generar diagrama Mermaid", "diagram"))
            actions.append(("Ver archivos de dist/", "view"))
        actions.append(("Recompilar", "recompile"))
        actions.append(("Cambiar opciones y recompilar", "options"))

        choice = _select(
            actions,
            message="¿Y ahora?",
            breadcrumb=breadcrumb,
            back_label="← Volver al menú",
            clear=False,
        )

        if choice is None:
            return
        if choice == "diagram":
            _diagram_flow(config, opts["channel"], breadcrumb)
            _pause()
        elif choice == "view":
            _browse_dist(config, breadcrumb)
        elif choice == "recompile":
            continue
        elif choice == "options":
            opts = _configure_opts(opts, breadcrumb)


def _flow_diagram() -> None:
    breadcrumb = "diagrama"
    config = _select(_list_configs(), message="Selecciona configuración", breadcrumb=breadcrumb)
    if config is None:
        _err_if_no_configs()
        return

    breadcrumb = f"diagrama  >  {config}"
    channel = _select(
        _list_channels(),
        message="Canal",
        breadcrumb=breadcrumb,
        back_label="(voice)",
    ) or "voice"

    while True:
        _diagram_flow(config, channel, breadcrumb)
        if not _confirm("¿Generar otro diagrama para este agente?", default=False):
            return


def _flow_view_dist() -> None:
    compiled = _list_compiled()
    if not compiled:
        _info("No hay compilaciones previas en dist/")
        _pause()
        return

    config = _select(compiled, message="Selecciona agente compilado", breadcrumb="dist")
    if config is None:
        return

    _browse_dist(config, f"dist  >  {config}")


def _browse_dist(config: str, breadcrumb: str) -> None:
    dist_path = DIST_DIR / config
    all_files = sorted(p for p in dist_path.rglob("*") if p.is_file())

    if not all_files:
        _info("La carpeta está vacía.")
        _pause()
        return

    while True:
        rel_names = [str(p.relative_to(dist_path)) for p in all_files]
        chosen = _select(
            rel_names,
            message="Selecciona archivo para abrir",
            breadcrumb=breadcrumb,
        )
        if chosen is None:
            return
        webbrowser.open((dist_path / chosen).as_uri())


def _err_if_no_configs() -> None:
    if not _list_configs():
        _err(f"No se encontraron configuraciones en {CONFIGS_DIR}/")
        _pause()


# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------

_MENU_ITEMS: list[tuple[str, object]] = [
    ("Compilar agente", _flow_compile),
    ("Generar diagrama Mermaid", _flow_diagram),
    ("Ver archivos de dist/", _flow_view_dist),
]


def _print_header() -> None:
    if _RICH:
        console.print(
            Panel.fit(
                "[bold cyan]Prompt Compiler[/bold cyan]  [dim]v2.0[/dim]\n"
                "[dim]Herramienta interactiva para compilar agentes conversacionales[/dim]",
                border_style="cyan",
                padding=(0, 4),
            )
        )
    else:
        print("\n== Prompt Compiler v2.0 ==")
        print("Compilador de agentes conversacionales\n")

    if not _INTERACTIVE and _QUESTIONARY:
        _info("Terminal no interactiva: usando menús numerados.")


def main() -> None:
    while True:
        _screen()
        _print_header()

        choice = _select(_MENU_ITEMS, message="Menú principal", back_label="Salir")

        if choice is None:
            _cprint("\n[dim]¡Hasta luego![/dim]\n", "\n¡Hasta luego!\n")
            break

        choice()  # type: ignore[operator]
        _pause()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nInterrumpido.")
        sys.exit(0)
