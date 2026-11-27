from __future__ import annotations

import base64
import io
import mimetypes
import re
import sys
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import unquote, urlsplit

from PIL import Image

from .drawio_layout import style_diagram as _style_diagram
from .models import DisclosureDiagram


DRAWIO_VERSION = "0.4.15"
ORIGIN = "http://127.0.0.1"
HOST_PAGE = """<!doctype html>
<meta charset="utf-8">
<script>
window.editorReady = false;
window.pending = null;
window.addEventListener("message", event => {
    const frame = document.getElementById("editor");
    if (event.origin !== location.origin || event.source !== frame.contentWindow) return;
    let data;
    try {
        data = typeof event.data === "string" ? JSON.parse(event.data) : event.data;
    } catch {
        return;
    }
    if (!data) return;
    if (data.event === "init") window.editorReady = true;
    if (window.pending && data.event === window.pending.event &&
        (!window.pending.format || data.format === window.pending.format)) {
        const {resolve, reject, timer} = window.pending;
        window.pending = null;
        clearTimeout(timer);
        if (data.error) reject(new Error(String(data.error)));
        else resolve(data);
    }
});
window.drawio = ({message, event, format}) => new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
        window.pending = null;
        reject(new Error("Draw.io 等待 " + (format || event) + " 超时"));
    }, 20000);
    window.pending = {resolve, reject, timer, event, format};
    document.getElementById("editor").contentWindow.postMessage(JSON.stringify(message), location.origin);
});
</script>
<iframe id="editor" width="1200" height="900"
 src="/drawio/index.html?embed=1&amp;proto=json&amp;offline=1&amp;stealth=1&amp;lang=zh&amp;dark=0"></iframe>
"""


def drawio_runtime_path() -> Path | None:
    if getattr(sys, "frozen", False):
        root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent / "_internal")) / "drawio"
    else:
        root = Path(__file__).resolve().parents[2] / "build" / "vendor" / f"drawio-{DRAWIO_VERSION}"
    return root if (root / "index.html").is_file() else None


@contextmanager
def drawio_renderer(runtime: Path):
    from playwright.sync_api import sync_playwright

    from .cnipa_upstream.browser import launch_chromium

    root = runtime.resolve()

    def serve(route) -> None:
        url = urlsplit(route.request.url)
        if url.scheme != "http" or url.netloc != "127.0.0.1" or route.request.method != "GET":
            route.abort()
        elif url.path == "/":
            route.fulfill(body=HOST_PAGE, content_type="text/html; charset=utf-8")
        elif url.path.startswith("/drawio/"):
            path = (root / unquote(url.path.removeprefix("/drawio/"))).resolve()
            if path.is_relative_to(root) and path.is_file():
                content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                route.fulfill(path=str(path), content_type=content_type)
            else:
                route.fulfill(status=404, body="")
        else:
            route.fulfill(status=404, body="")

    def render(diagram: DisclosureDiagram, image: Path, editable: Path) -> None:
        page = context.new_page()
        try:
            page.goto(ORIGIN + "/", wait_until="domcontentloaded", timeout=15_000)
            page.wait_for_function("window.editorReady", timeout=20_000)
            horizontal = diagram.kind == "系统架构图"
            source = re.sub(r"\A(\s*(?:flowchart|graph)\s+)(?:TD|TB|BT|RL|LR)\b",
                            r"\g<1>" + ("LR" if horizontal else "TD"),
                            diagram.mermaid, flags=re.IGNORECASE)
            _command(page, "load", {
                "descriptor": {"format": "mermaid", "data": source, "wrap": False},
                "title": diagram.title,
            })
            xml = _style_diagram(_command(page, "export", {"format": "xml"})["xml"],
                                 diagram.title, diagram.layout, horizontal=horizontal)
            _command(page, "load", {"xml": xml})
            result = _command(page, "export", {
                "format": "png", "scale": 2, "border": 16, "background": "#ffffff",
            })
            header, separator, data = result["data"].partition(",")
            if not separator or header != "data:image/png;base64":
                raise ValueError("Draw.io 未返回有效 PNG")
            png = base64.b64decode(data, validate=True)
            with Image.open(io.BytesIO(png)) as check:
                if check.format != "PNG":
                    raise ValueError("Draw.io 图像格式错误")
                check.verify()
            editable.write_text(xml, encoding="utf-8")
            image.write_bytes(png)
        finally:
            page.close()

    with sync_playwright() as playwright:
        browser, _label = launch_chromium(playwright, headless=True, prefer_bundled=True)
        try:
            with browser.new_context(service_workers="block") as context:
                context.route("**/*", serve)
                yield render
        finally:
            browser.close()


def _command(page, action: str, arguments: dict) -> dict:
    return page.evaluate("request => window.drawio(request)", {
        "message": {"action": action, **arguments},
        "event": action,
        "format": arguments.get("format"),
    })


