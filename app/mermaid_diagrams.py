from __future__ import annotations

import base64
import html as html_lib
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
CONFIGS_DIR = ROOT / "agents" / "defs"
DIST_DIR = ROOT / "dist"

_GO_TO_RE = re.compile(r"\bGO_TO:\s*(\w+)\b")

_SHAPES: dict[str, str] = {
    "decision": '{id}{{"{lbl}"}}',
    "message": '{id}["{lbl}"]',
    "question": '{id}[/"{lbl}"/]',
    "action": '{id}[["{lbl}"]]',
    "registration": '{id}[("{lbl}")]',
    "terminal": '{id}(["{lbl}"])',
    "start": '{id}(("{lbl}"))',
    "subflow_change": '{id}{{{{"{lbl}"}}}}',
}

_DEFAULT_SHAPE = '{id}["{lbl}"]'

_STYLES: dict[str, str] = {
    "decision": "fill:#f3e5f5,stroke:#7b1fa2,color:#333",
    "message": "fill:#e3f2fd,stroke:#1565c0,color:#333",
    "question": "fill:#fff9c4,stroke:#f57f17,color:#333",
    "action": "fill:#e8f5e9,stroke:#2e7d32,color:#333",
    "registration": "fill:#fff3e0,stroke:#e65100,color:#333",
    "terminal": "fill:#ffebee,stroke:#b71c1c,color:#333",
    "start": "fill:#eeeeee,stroke:#616161,color:#333",
    "subflow_change": "fill:#e0f2f1,stroke:#00695c,color:#333",
}


@dataclass(frozen=True)
class DiagramExportResult:
    mmd_path: Path
    output_path: Path
    method: str
    success: bool = True


def _state_type_value(value: Any) -> str:
    """
    Normaliza state.type.

    Soporta strings y enums.
    """
    return str(getattr(value, "value", value))


def _escape_label(label: str) -> str:
    """
    Escapa texto para labels Mermaid.
    """
    return (
        label.replace("\\", "\\\\")
        .replace('"', "'")
        .replace("\n", " ")
        .replace("\r", " ")
    )


def _node_decl(state_id: str, state_type: str) -> str:
    """
    Retorna la declaración Mermaid del nodo.
    """
    label = state_id.split("__")[-1] if "__" in state_id else state_id
    label = _escape_label(label)

    template = _SHAPES.get(state_type, _DEFAULT_SHAPE)
    return template.format(id=state_id, lbl=label)


def get_all_states(spec: Any) -> list[Any]:
    """
    Retorna estados normales + terminales.
    """
    return list(spec.states) + list(spec.terminal_states)


def get_subflow_prefixes(spec: Any) -> list[str]:
    """
    Retorna nombres de subflows detectados desde los state_id.

    Ejemplo:
        KYC__ASK_NAME -> kyc
    """
    states = get_all_states(spec)

    return sorted(
        {
            state.state_id.split("__")[0].lower()
            for state in states
            if "__" in state.state_id
        }
    )


def load_spec(config_name: str, channel: str = "voice") -> Any:
    """
    Carga el AgentSpec de una configuración.

    Este módulo vive en app/, por eso importa los loaders de app.
    """
    from app.loaders import load_agent_spec, load_channel_profile
    from app.schemas import CompilationParams

    config_dir = CONFIGS_DIR / config_name
    channel_profile = load_channel_profile(channel)
    params = CompilationParams()

    return load_agent_spec(config_dir, params, channel_profile)


def generate_mermaid(
    spec: Any,
    mode: str = "full",
    subflow_filter: str | None = None,
) -> str:
    """
    Genera código Mermaid flowchart TD desde un AgentSpec.

    Args:
        spec:
            AgentSpec cargado.
        mode:
            - "full": todos los estados, subflows agrupados en subgraph.
            - "root_only": solo estados sin prefijo de subflow.
            - "subflow": solo estados del subflow indicado.
        subflow_filter:
            Nombre del subflow cuando mode == "subflow".

    Returns:
        Código Mermaid como string.
    """
    all_states = get_all_states(spec)

    if mode == "root_only":
        all_states = [state for state in all_states if "__" not in state.state_id]

    elif mode == "subflow" and subflow_filter:
        prefix = subflow_filter.lower() + "__"

        all_states = [
            state
            for state in all_states
            if state.state_id.lower().startswith(prefix)
        ]

    state_type: dict[str, str] = {}
    edges: list[tuple[str, str, bool]] = []

    for state in all_states:
        state_id = state.state_id
        state_kind = _state_type_value(state.type)

        state_type[state_id] = state_kind

        for rule in state.route or []:
            for target in _GO_TO_RE.findall(rule):
                edges.append((state_id, target, False))

        for rule in state.fallback or []:
            for target in _GO_TO_RE.findall(rule):
                edges.append((state_id, target, True))

    if mode in ("root_only", "subflow"):
        valid_ids = set(state_type)

        edges = [
            (source, target, is_fallback)
            for source, target, is_fallback in edges
            if source in valid_ids and target in valid_ids
        ]

    lines: list[str] = ["flowchart TD"]

    subflows_map: dict[str, list[str]] = {}
    root_ids: list[str] = []

    for state_id in state_type:
        if "__" in state_id and mode == "full":
            prefix = state_id.split("__")[0].lower()
            subflows_map.setdefault(prefix, []).append(state_id)
        else:
            root_ids.append(state_id)

    for state_id in root_ids:
        lines.append(f"    {_node_decl(state_id, state_type[state_id])}")

    for prefix, state_ids in sorted(subflows_map.items()):
        lines.append(f'    subgraph {prefix}["{prefix.upper()}"]')

        for state_id in state_ids:
            lines.append(f"        {_node_decl(state_id, state_type[state_id])}")

        lines.append("    end")

    lines.append("")

    for source, target, _ in edges:
        lines.append(f"    {source} --> {target}")

    for index, (_, _, is_fallback) in enumerate(edges):
        if is_fallback:
            lines.append(
                f"    linkStyle {index} stroke:#ff0000,stroke-width:2px;"
            )

    lines.append("")

    for class_name, style in _STYLES.items():
        lines.append(f"    classDef {class_name} {style}")

    for state_id, kind in state_type.items():
        if kind in _STYLES:
            lines.append(f"    class {state_id} {kind}")

    return "\n".join(lines)


def generate_mermaid_from_config(
    config_name: str,
    channel: str = "voice",
    mode: str = "full",
    subflow_filter: str | None = None,
) -> str:
    """
    Helper directo: carga spec y genera Mermaid.
    """
    spec = load_spec(config_name, channel)

    return generate_mermaid(
        spec,
        mode=mode,
        subflow_filter=subflow_filter,
    )


def _try_mmdc(input_mmd: Path, output_png: Path) -> bool:
    """
    Intenta generar PNG con Mermaid CLI.

    Requiere tener instalado:
        npm install -g @mermaid-js/mermaid-cli
    """
    try:
        result = subprocess.run(
            [
                "mmdc",
                "-i",
                str(input_mmd),
                "-o",
                str(output_png),
                "-b",
                "white",
            ],
            capture_output=True,
            timeout=45,
        )

        return result.returncode == 0 and output_png.exists()

    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _try_mermaid_ink(mermaid_code: str, output_png: Path) -> bool:
    """
    Descarga PNG desde mermaid.ink.

    Requiere internet.
    """
    encoded = base64.urlsafe_b64encode(
        mermaid_code.encode("utf-8")
    ).decode("ascii")

    url = f"https://mermaid.ink/img/{encoded}?bgColor=white"

    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "prompt-compiler/1.0"},
        )

        with urllib.request.urlopen(request, timeout=20) as response:
            data = response.read()

        if len(data) < 500:
            return False

        output_png.write_bytes(data)

        return True

    except (urllib.error.URLError, OSError):
        return False


def _human_friendly(name: str) -> str:
    """'babynova_fertility_voice' → 'Babynova fertility voice'"""
    s = name.replace("_", " ")
    return s[:1].upper() + s[1:] if s else s


def save_html(
    mermaid_code: str,
    output_html: Path,
    diagram_title: str | None = None,
) -> None:
    """
    Genera un HTML que renderiza el diagrama con zoom/pan interactivo y centrado.
    """
    safe_mermaid = html_lib.escape(mermaid_code)
    title = diagram_title or output_html.stem

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <title>Diagrama de Estados — {title}</title>
  <script src="https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js"></script>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      background: #f0f0f0;
      font-family: sans-serif;
      display: flex;
      flex-direction: column;
      height: 100vh;
      overflow: hidden;
    }}

    /* ── Toolbar ─────────────────────────────────────────── */
    .toolbar {{
      background: white;
      border-bottom: 1px solid #ddd;
      padding: 8px 16px;
      display: flex;
      align-items: center;
      gap: 8px;
      flex-shrink: 0;
      box-shadow: 0 1px 4px rgba(0,0,0,.08);
    }}

    .toolbar h2 {{
      font-size: 14px;
      color: #444;
      font-weight: 600;
      flex: 1;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}

    .btn {{
      background: #f5f5f5;
      border: 1px solid #ddd;
      border-radius: 6px;
      padding: 5px 12px;
      cursor: pointer;
      font-size: 15px;
      font-weight: bold;
      color: #333;
      user-select: none;
      line-height: 1;
      transition: background .12s;
    }}
    .btn:hover  {{ background: #e8e8e8; }}
    .btn:active {{ background: #d8d8d8; }}

    .zoom-label {{
      font-size: 13px;
      color: #555;
      min-width: 46px;
      text-align: center;
      font-variant-numeric: tabular-nums;
    }}

    .hint {{
      font-size: 11px;
      color: #aaa;
      margin-left: 4px;
    }}

    /* ── Viewport (pan/zoom stage) ───────────────────────── */
    .viewport {{
      flex: 1;
      overflow: hidden;
      position: relative;
      cursor: grab;
      display: flex;
      align-items: center;
      justify-content: center;
    }}
    .viewport.panning {{ cursor: grabbing; }}

    .canvas {{
      transform-origin: center center;
      display: inline-flex;
      align-items: center;
      justify-content: center;
    }}

    /* ── Legend ──────────────────────────────────────────── */
    .legend {{
      position: fixed;
      bottom: 14px;
      right: 14px;
      background: white;
      border-radius: 8px;
      padding: 8px 12px;
      box-shadow: 0 2px 10px rgba(0,0,0,.15);
      display: flex;
      flex-direction: column;
      gap: 5px;
      font-size: 11px;
      pointer-events: none;
    }}

    .legend-title {{
      font-weight: 700;
      font-size: 12px;
      color: #333;
      margin-bottom: 2px;
    }}

    .legend span {{
      display: flex;
      align-items: center;
      gap: 5px;
      color: #555;
    }}

    .dot {{
      width: 11px;
      height: 11px;
      border-radius: 2px;
      flex-shrink: 0;
    }}
  </style>
</head>
<body>

  <div class="toolbar">
    <h2>Diagrama — {title}</h2>
    <button class="btn" onclick="zoomOut()" title="Alejar (−)">−</button>
    <span class="zoom-label" id="zoom-label">100%</span>
    <button class="btn" onclick="zoomIn()"  title="Acercar (+)">+</button>
    <button class="btn" onclick="resetView()" title="Restablecer vista (R)" style="font-size:13px;padding:5px 10px;">⌂ Reset</button>
    <span class="hint">Rueda para zoom · Arrastrar para mover · + / − / R</span>
  </div>

  <div class="viewport" id="viewport">
    <div class="canvas" id="canvas">
      <pre class="mermaid">{safe_mermaid}</pre>
    </div>
  </div>

  <div class="legend">
    <div class="legend-title">Leyenda</div>
    <span><div class="dot" style="background:#eeeeee;border:1px solid #616161"></div>start</span>
    <span><div class="dot" style="background:#e3f2fd;border:1px solid #1565c0"></div>message</span>
    <span><div class="dot" style="background:#fff9c4;border:1px solid #f57f17"></div>question</span>
    <span><div class="dot" style="background:#f3e5f5;border:1px solid #7b1fa2"></div>decision</span>
    <span><div class="dot" style="background:#fff3e0;border:1px solid #e65100"></div>registration</span>
    <span><div class="dot" style="background:#e8f5e9;border:1px solid #2e7d32"></div>action</span>
    <span><div class="dot" style="background:#e0f2f1;border:1px solid #00695c"></div>subflow_change</span>
    <span><div class="dot" style="background:#ffebee;border:1px solid #b71c1c"></div>terminal</span>
    <span>
      <svg width="22" height="10" xmlns="http://www.w3.org/2000/svg" style="flex-shrink:0">
        <line x1="1" y1="5" x2="15" y2="5" stroke="#333" stroke-width="1.5"/>
        <polygon points="13,2 21,5 13,8" fill="#333"/>
      </svg>
      conexión
    </span>
    <span>
      <svg width="22" height="10" xmlns="http://www.w3.org/2000/svg" style="flex-shrink:0">
        <line x1="1" y1="5" x2="15" y2="5" stroke="#ff0000" stroke-width="1.5"/>
        <polygon points="13,2 21,5 13,8" fill="#ff0000"/>
      </svg>
      fallback
    </span>
  </div>

  <script>
    mermaid.initialize({{
      startOnLoad: true,
      theme: "default",
      maxTextSize: 1000000, 
      flowchart: {{ useMaxWidth: false, htmlLabels: true }}
    }});

    const viewport  = document.getElementById('viewport');
    const canvas    = document.getElementById('canvas');
    const zoomLabel = document.getElementById('zoom-label');

    let scale = 1, panX = 0, panY = 0;
    let isPanning = false, startX = 0, startY = 0;

    function update() {{
      canvas.style.transform = `translate(${{panX}}px, ${{panY}}px) scale(${{scale}})`;
      zoomLabel.textContent  = Math.round(scale * 100) + '%';
    }}

    function zoomIn()    {{ scale = Math.min(scale * 1.2, 20);   update(); }}
    function zoomOut()   {{ scale = Math.max(scale / 1.2, 0.05); update(); }}
    function resetView() {{ scale = 1; panX = 0; panY = 0;       update(); }}

    /* ── Mouse wheel: zoom toward cursor ─────────────────── */
    viewport.addEventListener('wheel', function(e) {{
      e.preventDefault();
      const factor   = e.deltaY < 0 ? 1.12 : 1 / 1.12;
      const newScale = Math.min(Math.max(scale * factor, 0.05), 20);
      const rect     = viewport.getBoundingClientRect();
      const mx = e.clientX - rect.left - rect.width  / 2;
      const my = e.clientY - rect.top  - rect.height / 2;
      panX = mx - (mx - panX) * (newScale / scale);
      panY = my - (my - panY) * (newScale / scale);
      scale = newScale;
      update();
    }}, {{ passive: false }});

    /* ── Mouse drag: pan ─────────────────────────────────── */
    viewport.addEventListener('mousedown', function(e) {{
      if (e.button !== 0) return;
      isPanning = true;
      startX = e.clientX - panX;
      startY = e.clientY - panY;
      viewport.classList.add('panning');
    }});
    document.addEventListener('mousemove', function(e) {{
      if (!isPanning) return;
      panX = e.clientX - startX;
      panY = e.clientY - startY;
      update();
    }});
    document.addEventListener('mouseup', function() {{
      isPanning = false;
      viewport.classList.remove('panning');
    }});

    /* ── Touch: drag + pinch zoom ────────────────────────── */
    let lastTouchDist = 0;
    viewport.addEventListener('touchstart', function(e) {{
      if (e.touches.length === 1) {{
        isPanning = true;
        startX = e.touches[0].clientX - panX;
        startY = e.touches[0].clientY - panY;
      }} else if (e.touches.length === 2) {{
        isPanning = false;
        lastTouchDist = Math.hypot(
          e.touches[0].clientX - e.touches[1].clientX,
          e.touches[0].clientY - e.touches[1].clientY
        );
      }}
    }}, {{ passive: true }});
    viewport.addEventListener('touchmove', function(e) {{
      e.preventDefault();
      if (e.touches.length === 1 && isPanning) {{
        panX = e.touches[0].clientX - startX;
        panY = e.touches[0].clientY - startY;
        update();
      }} else if (e.touches.length === 2) {{
        const dist = Math.hypot(
          e.touches[0].clientX - e.touches[1].clientX,
          e.touches[0].clientY - e.touches[1].clientY
        );
        scale = Math.min(Math.max(scale * (dist / lastTouchDist), 0.05), 20);
        lastTouchDist = dist;
        update();
      }}
    }}, {{ passive: false }});
    viewport.addEventListener('touchend', function() {{ isPanning = false; }});

    /* ── Keyboard shortcuts ──────────────────────────────── */
    document.addEventListener('keydown', function(e) {{
      if (e.target.tagName === 'INPUT') return;
      if (e.key === '+' || e.key === '=') zoomIn();
      else if (e.key === '-')             zoomOut();
      else if (e.key === '0' || e.key === 'r' || e.key === 'R') resetView();
    }});
  </script>
</body>
</html>"""

    output_html.write_text(html, encoding="utf-8")


def export_diagram(
    mermaid_code: str,
    output_dir: Path,
    suffix: str = "",
    config_name: str | None = None,
    subflow_filter: str | None = None,
) -> DiagramExportResult:
    """
    Exporta siempre el diagrama como HTML.

    También guarda el archivo .mmd con el código Mermaid.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    mmd_path = output_dir / f"diagram{suffix}.mmd"
    html_path = output_dir / f"diagram{suffix}.html"

    mmd_path.write_text(mermaid_code, encoding="utf-8")

    diagram_title: str | None = None
    if config_name:
        diagram_title = _human_friendly(config_name)
        if subflow_filter:
            diagram_title += f" — {_human_friendly(subflow_filter)}"

    save_html(mermaid_code, html_path, diagram_title=diagram_title)

    return DiagramExportResult(
        mmd_path=mmd_path,
        output_path=html_path,
        method="html",
    )