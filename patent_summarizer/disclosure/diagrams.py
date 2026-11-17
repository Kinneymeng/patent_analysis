from __future__ import annotations

import re
import uuid
from dataclasses import replace
from pathlib import Path
import sys

from .models import DisclosureDiagnostic, DisclosureDiagram, TechnicalDisclosure


_NODE_PATTERNS = (
    ("[", re.compile(r'(?P<id>[A-Za-z][A-Za-z0-9_]*)\s*\[\s*"?(?P<label>[^\]";]+)"?\s*\]')),
    ("{", re.compile(r'(?P<id>[A-Za-z][A-Za-z0-9_]*)\s*\{\s*"?(?P<label>[^\}";]+)"?\s*\}')),
    ("(", re.compile(r'(?P<id>[A-Za-z][A-Za-z0-9_]*)\s*\(\s*"?(?P<label>[^\)";]+)"?\s*\)')),
)


def render_disclosure_diagrams(
    disclosure: TechnicalDisclosure,
    assets_dir: Path,
    *,
    diagnostics: list[DisclosureDiagnostic] | None = None,
    allow_unrendered: bool = False,
) -> TechnicalDisclosure:
    if not disclosure.diagrams:
        return disclosure
    assets_dir.mkdir(parents=True, exist_ok=True)
    runtime = _mermaid_runtime_path()
    if runtime is not None:
        try:
            return _render_diagrams_with_shared_browser(
                disclosure,
                assets_dir,
                runtime,
                diagnostics,
                allow_unrendered=allow_unrendered,
            )
        except RuntimeError as exc:
            browser_error = str(exc)
    else:
        browser_error = "本地 Mermaid 运行时不可用"
    rendered: list[DisclosureDiagram] = []
    for index, diagram in enumerate(disclosure.diagrams, start=1):
        output = assets_dir / f"diagram_{index:02d}.png"
        try:
            _render_with_pillow(_normalize_mermaid(diagram.mermaid), output, diagram.title)
        except Exception as exc:
            if allow_unrendered:
                _record_unrendered(diagnostics, diagram.title, str(exc))
                rendered.append(diagram)
                continue
            raise RuntimeError(
                f"Mermaid 与备用图示转换均失败：{browser_error}；Pillow: {exc}"
            ) from exc
        _record_pillow_fallback(diagnostics, diagram.title, browser_error)
        rendered.append(replace(diagram, image_path=output))
    return replace(disclosure, diagrams=tuple(rendered))


def _render_diagrams_with_shared_browser(
    disclosure: TechnicalDisclosure,
    assets_dir: Path,
    runtime: Path,
    diagnostics: list[DisclosureDiagnostic] | None,
    *,
    allow_unrendered: bool,
) -> TechnicalDisclosure:
    try:
        from playwright.sync_api import sync_playwright
        from .cnipa_upstream.browser import launch_chromium
    except ImportError as exc:
        raise RuntimeError("Mermaid 图示生成缺少便携包内的 Playwright 组件") from exc
    rendered: list[DisclosureDiagram] = []
    try:
        with sync_playwright() as playwright:
            browser, _label = launch_chromium(playwright, headless=True)
            context = browser.new_context(
                viewport={"width": 1600, "height": 1200},
                device_scale_factor=2,
            )
            try:
                page = context.new_page()
                for index, diagram in enumerate(disclosure.diagrams, start=1):
                    output = assets_dir / f"diagram_{index:02d}.png"
                    errors = []
                    for candidate in (diagram.mermaid, _normalize_mermaid(diagram.mermaid)):
                        if candidate != diagram.mermaid or not errors:
                            try:
                                _render_mermaid_page(page, candidate, output, diagram.title, runtime)
                                break
                            except Exception as exc:
                                errors.append(f"{type(exc).__name__}: {exc}")
                    else:
                        try:
                            _render_with_pillow(
                                _normalize_mermaid(diagram.mermaid),
                                output,
                                diagram.title,
                            )
                        except Exception as exc:
                            if allow_unrendered:
                                _record_unrendered(diagnostics, diagram.title, str(exc))
                                rendered.append(diagram)
                                continue
                            raise RuntimeError(
                                f"{diagram.title} 的 Mermaid 与 Pillow 转换均失败："
                                + "；".join(errors)
                                + f"；Pillow: {exc}"
                            ) from exc
                        _record_pillow_fallback(
                            diagnostics,
                            diagram.title,
                            "；".join(errors),
                        )
                    rendered.append(replace(diagram, image_path=output))
            finally:
                context.close()
                browser.close()
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Mermaid 共享浏览器启动或渲染失败：{exc}") from exc
    return replace(disclosure, diagrams=tuple(rendered))


def _record_unrendered(
    diagnostics: list[DisclosureDiagnostic] | None, title: str, error: str
) -> None:
    if diagnostics is not None:
        diagnostics.append(DisclosureDiagnostic(
            "diagram-export", "diagram-unrendered", f"{title} 无法生成图片：{error}",
            "已在对应位置保留图题及待补提示，Markdown 附原图源", requires_review=True,
        ))


def render_mermaid_flowchart(
    source: str,
    output: Path,
    title: str = "",
    *,
    diagnostics: list[DisclosureDiagnostic] | None = None,
) -> Path:
    runtime = _mermaid_runtime_path()
    normalized = _normalize_mermaid(source)
    browser_errors: list[str] = []
    if runtime is not None:
        for candidate in (source, normalized):
            if candidate != source or not browser_errors:
                try:
                    return _render_with_mermaid(candidate, output, title, runtime)
                except RuntimeError as exc:
                    browser_errors.append(str(exc))
    try:
        result = _render_with_pillow(normalized, output, title)
    except Exception as exc:
        detail = "；".join(browser_errors[-2:])
        if detail:
            raise RuntimeError(f"Mermaid 与备用图示转换均失败：{detail}；Pillow: {exc}") from exc
        raise
    if diagnostics is not None:
        _record_pillow_fallback(diagnostics, title, "浏览器渲染不可用")
    return result


def _render_with_pillow(source: str, output: Path, title: str) -> Path:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError("图示生成需要 Pillow；便携包构建必须包含该依赖") from exc
    nodes, shapes, edges = _parse_flowchart(source)
    if len(nodes) < 2 or not edges:
        raise ValueError("Mermaid 图示至少需要两个相连节点")
    levels = _layout_levels(nodes, edges)
    max_columns = max(len(values) for values in levels.values())
    node_width, node_height = 270, 86
    horizontal_gap, vertical_gap = 70, 100
    margin_x, margin_y = 60, 90
    title_height = 60 if title else 20
    width = max(800, margin_x * 2 + max_columns * node_width + max(0, max_columns - 1) * horizontal_gap)
    height = margin_y + title_height + len(levels) * node_height + max(0, len(levels) - 1) * vertical_gap + 70
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    font = _font(ImageFont, 24)
    title_font = _font(ImageFont, 28)
    if title:
        draw.text((width // 2, 28), title, fill="#172033", font=title_font, anchor="ma")
    positions: dict[str, tuple[int, int, int, int]] = {}
    for level, identifiers in levels.items():
        row_width = len(identifiers) * node_width + max(0, len(identifiers) - 1) * horizontal_gap
        start_x = (width - row_width) // 2
        y = margin_y + title_height + level * (node_height + vertical_gap)
        for column, identifier in enumerate(identifiers):
            x = start_x + column * (node_width + horizontal_gap)
            positions[identifier] = (x, y, x + node_width, y + node_height)
    for start, end in edges:
        if start not in positions or end not in positions:
            continue
        sx1, sy1, sx2, sy2 = positions[start]
        ex1, ey1, ex2, ey2 = positions[end]
        start_point = ((sx1 + sx2) // 2, sy2)
        end_point = ((ex1 + ex2) // 2, ey1)
        _arrow(draw, start_point, end_point)
    for identifier, bounds in positions.items():
        fill = "#EDF4FF" if shapes.get(identifier) != "{" else "#FFF4D6"
        draw.rounded_rectangle(bounds, radius=16, fill=fill, outline="#315C9B", width=3)
        _draw_wrapped(draw, bounds, nodes[identifier], font)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.stem}.{uuid.uuid4().hex}.tmp.png")
    try:
        image.save(temporary, format="PNG", optimize=True)
        with Image.open(temporary) as check:
            check.verify()
        temporary.replace(output)
    finally:
        if temporary.exists():
            temporary.unlink()
    return output


def _normalize_mermaid(source: str) -> str:
    lines = []
    for index, raw in enumerate((source or "").splitlines()):
        line = raw.strip()
        if not line:
            continue
        if index == 0:
            lines.append("flowchart TD")
            continue
        if line.startswith(("click ", "style ", "classDef ", "linkStyle ")):
            continue
        line = re.sub(r"<br\s*/?>", " ", line, flags=re.IGNORECASE)
        line = line.replace("；", ";")
        lines.append(line)
    if not lines:
        return "flowchart TD"
    if not lines[0].startswith("flowchart"):
        lines.insert(0, "flowchart TD")
    return "\n".join(lines)


def _mermaid_runtime_path() -> Path | None:
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        candidates.append(
            Path(sys.executable).resolve().parent / "_internal" / "mermaid" / "mermaid.min.js"
        )
    candidates.extend(
        (
            Path(__file__).resolve().parents[2] / "build" / "vendor" / "mermaid.min.js",
            Path(__file__).resolve().with_name("mermaid.min.js"),
        )
    )
    return next((path for path in candidates if path.is_file()), None)


def _render_with_mermaid(source: str, output: Path, title: str, runtime: Path) -> Path:
    try:
        from playwright.sync_api import sync_playwright
        from .cnipa_upstream.browser import launch_chromium
    except ImportError as exc:
        raise RuntimeError("Mermaid 图示生成缺少便携包内的 Playwright 或 Pillow 组件") from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.stem}.{uuid.uuid4().hex}.tmp.png")
    try:
        with sync_playwright() as playwright:
            browser, _label = launch_chromium(playwright, headless=True)
            context = browser.new_context(
                viewport={"width": 1600, "height": 1200},
                device_scale_factor=2,
            )
            try:
                page = context.new_page()
                _render_mermaid_page(page, source, temporary, title, runtime)
            finally:
                context.close()
                browser.close()
        temporary.replace(output)
        return output
    except Exception as exc:
        raise RuntimeError(f"Mermaid 图示本地转换失败：{exc}") from exc
    finally:
        if temporary.exists():
            temporary.unlink()


def _render_mermaid_page(page, source: str, output: Path, title: str, runtime: Path) -> None:
    from PIL import Image

    page.set_content(
        "<html><body style='margin:0;padding:32px;background:white'>"
        "<h2 id='title'></h2><div id='graph'></div></body></html>"
    )
    page.add_script_tag(path=str(runtime))
    page.evaluate(
        """async ({code, title, graphId}) => {
            document.getElementById('title').textContent = title;
            mermaid.initialize({startOnLoad: false, securityLevel: 'strict', theme: 'default'});
            const rendered = await mermaid.render(graphId, code);
            document.getElementById('graph').innerHTML = rendered.svg;
        }""",
        {"code": source, "title": title, "graphId": f"graph-{uuid.uuid4().hex}"},
    )
    page.locator("body").screenshot(path=str(output), animations="disabled")
    with Image.open(output) as check:
        check.verify()


def _record_pillow_fallback(
    diagnostics: list[DisclosureDiagnostic] | None,
    title: str,
    reason: str,
) -> None:
    if diagnostics is None:
        return
    diagnostics.append(
        DisclosureDiagnostic(
            stage="diagram-export",
            code="pillow-fallback",
            message=f"{title or '图示'} 的 Mermaid 浏览器渲染未完成：{reason}",
            action="已使用本地 Pillow 备用渲染器生成有效 PNG",
        )
    )


def _parse_flowchart(source: str):
    nodes: dict[str, str] = {}
    shapes: dict[str, str] = {}
    edges: list[tuple[str, str]] = []
    for line in source.splitlines()[1:]:
        stripped = line.strip()
        if not stripped or stripped.startswith(("%%", "subgraph", "end", "direction", "class")):
            continue
        for opening, pattern in _NODE_PATTERNS:
            for match in pattern.finditer(stripped):
                identifier = match.group("id")
                label = re.sub(r"<br\s*/?>", " ", match.group("label"), flags=re.IGNORECASE).strip()
                nodes[identifier] = label
                shapes[identifier] = opening
        if "-->" in stripped or "---" in stripped or "==>" in stripped:
            linked: list[str] = []
            for segment in re.split(r"(?:-->|---|==>)", stripped):
                segment = re.sub(r"^\s*\|[^|]*\|\s*", "", segment)
                match = re.match(r"\s*([A-Za-z][A-Za-z0-9_]*)", segment)
                if match:
                    linked.append(match.group(1))
            for start, end in zip(linked, linked[1:]):
                if start != end:
                    edges.append((start, end))
    # Lines such as A --> B can omit repeated node declarations.
    for start, end in list(edges):
        nodes.setdefault(start, start)
        nodes.setdefault(end, end)
        shapes.setdefault(start, "[")
        shapes.setdefault(end, "[")
    return nodes, shapes, _dedupe_edges(edges)


def _layout_levels(nodes: dict[str, str], edges: list[tuple[str, str]]) -> dict[int, list[str]]:
    incoming = {name: 0 for name in nodes}
    outgoing: dict[str, list[str]] = {name: [] for name in nodes}
    for start, end in edges:
        incoming[end] = incoming.get(end, 0) + 1
        outgoing.setdefault(start, []).append(end)
    queue = [name for name in nodes if incoming.get(name, 0) == 0]
    if not queue:
        queue = [next(iter(nodes))]
    levels_for = {name: 0 for name in queue}
    visited: set[str] = set()
    while queue:
        current = queue.pop(0)
        visited.add(current)
        for target in outgoing.get(current, []):
            levels_for[target] = max(levels_for.get(target, 0), levels_for[current] + 1)
            incoming[target] -= 1
            if incoming[target] <= 0 and target not in visited:
                queue.append(target)
    for name in nodes:
        levels_for.setdefault(name, max(levels_for.values(), default=-1) + 1)
    grouped: dict[int, list[str]] = {}
    for name in nodes:
        grouped.setdefault(levels_for[name], []).append(name)
    return dict(sorted(grouped.items()))


def _arrow(draw, start: tuple[int, int], end: tuple[int, int]) -> None:
    sx, sy = start
    ex, ey = end
    middle_y = sy + max(20, (ey - sy) // 2)
    points = [(sx, sy), (sx, middle_y), (ex, middle_y), (ex, ey - 10)]
    draw.line(points, fill="#526277", width=4, joint="curve")
    draw.polygon([(ex, ey), (ex - 8, ey - 14), (ex + 8, ey - 14)], fill="#526277")


def _draw_wrapped(draw, bounds, text: str, font) -> None:
    x1, y1, x2, y2 = bounds
    max_chars = max(8, (x2 - x1) // 24)
    lines = [text[index:index + max_chars] for index in range(0, len(text), max_chars)] or [text]
    lines = lines[:3]
    line_height = 29
    start_y = (y1 + y2 - len(lines) * line_height) // 2
    for index, line in enumerate(lines):
        draw.text(((x1 + x2) // 2, start_y + index * line_height), line, fill="#172033", font=font, anchor="ma")


def _font(image_font, size: int):
    candidates = (
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    for path in candidates:
        if Path(path).exists():
            return image_font.truetype(path, size=size)
    return image_font.load_default()


def _dedupe_edges(edges: list[tuple[str, str]]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for edge in edges:
        if edge not in result:
            result.append(edge)
    return result


__all__ = ["render_disclosure_diagrams", "render_mermaid_flowchart"]
