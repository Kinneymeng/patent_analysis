from __future__ import annotations

import html
import re
import textwrap
from dataclasses import dataclass
from xml.etree import ElementTree as ET


PALETTE = {
    "input": ("#e8edf5", "#7589a2"),
    "process": ("#e2edf9", "#6d90b7"),
    "output": ("#e4f1e4", "#769d76"),
    "exception": ("#f9e7dc", "#b88665"),
    "decision": ("#fff2cf", "#ad9145"),
}


@dataclass
class _Node:
    owner: ET.Element
    cell: ET.Element
    geometry: ET.Element
    name: str
    label: str
    lines: list[str]
    decision: bool
    x: float
    y: float
    width: int
    height: int

    @property
    def box(self) -> tuple[float, float, float, float]:
        return (self.x - self.width / 2, self.y - self.height / 2,
                self.x + self.width / 2, self.y + self.height / 2)


def style_diagram(xml: str, title: str, layout=None, *, horizontal: bool = False) -> str:
    document = ET.fromstring(xml)
    if document.tag == "mxGraphModel":
        wrapper = ET.Element("mxfile")
        ET.SubElement(wrapper, "diagram", name=title).append(document)
        document = wrapper
    page = document.find("diagram")
    root = page.find("mxGraphModel/root") if page is not None else None
    if document.tag != "mxfile" or root is None:
        raise ValueError("Draw.io 未返回可编辑的图形数据")
    page.set("name", title)
    page.find("mxGraphModel").set("background", "#ffffff")

    nodes: dict[str, _Node] = {}
    edges = []
    for owner in root:
        cell = owner if owner.tag == "mxCell" else owner.find("mxCell")
        if cell is None:
            continue
        if cell.get("edge") == "1":
            edges.append(cell)
        elif cell.get("vertex") == "1":
            nodes[owner.get("id")] = _make_node(owner, cell)
    if not nodes or not edges:
        raise ValueError("Draw.io 图中缺少节点或连线")

    # Imported subgraphs already have their own parent coordinate systems.
    # Keep those placements unless the graph is flat.
    flat = all(node.cell.get("parent") == "1" for node in nodes.values())
    by_name = {node.name: node for node in nodes.values()}
    proposed = _apply_layout(by_name, layout) if flat else False
    if flat and not proposed:
        _arrange_ranks(list(nodes.values()), horizontal)
    incoming = {edge.get("target") for edge in edges}
    outgoing = {edge.get("source") for edge in edges}
    for identifier, node in nodes.items():
        role = _role(node, identifier, layout if proposed else None, incoming, outgoing)
        _format_node(node, role, reposition=flat)

    max_x = max(node.box[2] for node in nodes.values())
    max_y = max(node.box[3] for node in nodes.values())
    routes = _planned_routes(layout if proposed else None)
    loop_count = 0
    for edge in edges:
        source = nodes.get(edge.get("source"))
        destination = nodes.get(edge.get("target"))
        if flat and source and destination:
            reverse = (not proposed and destination.x < source.x - 20 if horizontal
                       else destination.y < source.y - 20) or source is destination
            loop_count += int(reverse)
            outside = (max_y if horizontal else max_x) + 28 + 18 * loop_count
            waypoints = routes.get((source.name, destination.name))
            _format_edge(edge, source, destination, horizontal, reverse, outside, waypoints)
        else:
            names, style = _read_style(edge)
            style.update(edgeStyle="orthogonalEdgeStyle", curved="0", rounded="0")
            _write_style(edge, names, style)
    if proposed:
        _add_groups(root, by_name, layout)
    return ET.tostring(document, encoding="unicode")


def _make_node(owner: ET.Element, cell: ET.Element) -> _Node:
    geometry = cell.find("mxGeometry")
    if geometry is None:
        geometry = ET.SubElement(cell, "mxGeometry", {"as": "geometry"})
    names, style = _read_style(cell)
    decision = "rhombus" in names or style.get("shape") == "rhombus"
    raw = owner.get("label", "") if owner is not cell else cell.get("value", "")
    label = html.unescape(re.sub(r"<[^>]+>", " ", raw)).strip()
    lines = textwrap.wrap(label, width=8 if decision else 17, break_on_hyphens=False) or [label]
    width = 160 if decision else 168
    height = max(84, len(lines) * 17 + 36) if decision else max(48, len(lines) * 18 + 18)
    x = float(geometry.get("x", 0)) + float(geometry.get("width", width)) / 2
    y = float(geometry.get("y", 0)) + float(geometry.get("height", height)) / 2
    name = owner.get("mermaidId", "").removeprefix("n:") or owner.get("id")
    return _Node(owner, cell, geometry, name, label, lines, decision, x, y, width, height)


def _read_style(cell):
    parts = cell.get("style", "").split(";")
    return ([item for item in parts if item and "=" not in item],
            dict(item.split("=", 1) for item in parts if "=" in item))


def _write_style(cell, names, style) -> None:
    cell.set("style", ";".join([*names, *(f"{key}={value}" for key, value in style.items())]) + ";")


def _point(value) -> bool:
    return (isinstance(value, list) and len(value) >= 2
            and all(type(number) is int and 0 <= number <= 4096 for number in value[:2]))


def _apply_layout(nodes: dict[str, _Node], layout) -> bool:
    positions = layout.get("节点") if layout else None
    if not isinstance(positions, dict) or positions.keys() != nodes.keys():
        return False
    occupied = []
    for name, node in nodes.items():
        position = positions[name]
        if not _point(position):
            return False
        x, y = position[:2]
        box = (x - node.width / 2 - 8, y - node.height / 2 - 8,
               x + node.width / 2 + 8, y + node.height / 2 + 8)
        if any(_overlap(box, previous) for previous in occupied):
            return False
        occupied.append(box)
    for name, node in nodes.items():
        node.x, node.y = positions[name][:2]
    return True


def _overlap(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _arrange_ranks(nodes: list[_Node], horizontal: bool) -> None:
    primary = (lambda node: node.x) if horizontal else (lambda node: node.y)
    secondary = (lambda node: node.y) if horizontal else (lambda node: node.x)
    major = (lambda node: node.width) if horizontal else (lambda node: node.height)
    minor = (lambda node: node.height) if horizontal else (lambda node: node.width)
    ranks: list[list[_Node]] = []
    for node in sorted(nodes, key=primary):
        if not ranks or primary(node) - primary(ranks[-1][0]) > 24:
            ranks.append([])
        ranks[-1].append(node)
    along = 32
    for rank in ranks:
        thickness = max(map(major, rank))
        across = -(sum(map(minor, rank)) + 48 * (len(rank) - 1)) / 2
        for node in sorted(rank, key=secondary):
            center_along, center_across = along + thickness / 2, across + minor(node) / 2
            node.x, node.y = (center_along, center_across) if horizontal else (center_across, center_along)
            across += minor(node) + 48
        along += thickness + 44


def _role(node: _Node, identifier: str, layout, incoming, outgoing) -> str:
    if node.decision:
        return "decision"
    proposed = layout["节点"][node.name][2:] if layout else []
    if proposed and proposed[0] in ("input", "process", "output", "exception"):
        return proposed[0]
    if re.search("异常|回退|降级|保持", node.label):
        return "exception"
    if identifier not in incoming:
        return "input"
    return "process" if identifier in outgoing else "output"


def _format_node(node: _Node, role: str, *, reposition: bool) -> None:
    names, style = _read_style(node.cell)
    fill, stroke = PALETTE[role]
    style.update(fillColor=fill, strokeColor=stroke, strokeWidth="1.3", fontColor="#182534",
                 fontFamily="Microsoft YaHei", fontSize="13", html="1", whiteSpace="wrap",
                 align="center", verticalAlign="middle", shadow="0", rounded="0" if node.decision else "1")
    _write_style(node.cell, names, style)
    if reposition:
        x, y, _, _ = node.box
        node.geometry.attrib.update(x=str(x), y=str(y), width=str(node.width), height=str(node.height))
        attribute = "value" if node.owner is node.cell else "label"
        node.owner.set(attribute, "<br>".join(html.escape(line) for line in node.lines))


def _planned_routes(layout) -> dict:
    raw = layout.get("折线") if layout else None
    result = {}
    for route in raw if isinstance(raw, list) else ():
        if not isinstance(route, dict):
            continue
        start, end, points = route.get("起点"), route.get("终点"), route.get("折点")
        if (isinstance(start, str) and isinstance(end, str) and isinstance(points, list)
                and 1 <= len(points) <= 8 and all(_point(point) for point in points)):
            result[start, end] = [point[:2] for point in points]
    return result


def _format_edge(edge, source, destination, horizontal, reverse, outside, waypoints) -> None:
    names, style = _read_style(edge)
    if reverse and horizontal:
        ports = (0.5, 1, 0.5, 1)
        fallback = [(source.x, outside), (destination.x, outside)]
    elif reverse:
        ports = (1, 0.5, 1, 0.5)
        fallback = [(outside, source.y), (outside, destination.y)]
    elif (abs(destination.y - source.y) < 24
          or (horizontal and abs(destination.x - source.x) > abs(destination.y - source.y))
          or (source.decision and abs(destination.x - source.x) > 35)):
        forward = destination.x > source.x
        ports = (1, 0.5, 0, 0.5) if forward else (0, 0.5, 1, 0.5)
        if not horizontal and destination.y > source.y + 24:
            ports = (*ports[:2], 0.5, 0)
        fallback = []
    else:
        ports = (0.5, 1, 0.5, 0) if destination.y > source.y else (0.5, 0, 0.5, 1)
        fallback = []
    style.update(edgeStyle="orthogonalEdgeStyle", curved="0", rounded="0", noEdgeStyle="0",
                 endArrow="block", endSize="7", strokeColor="#64748b" if reverse else "#465568",
                 dashed="1" if reverse else style.get("dashed", "0"), strokeWidth="1.4",
                 fontColor="#29394a", fontSize="12", fontFamily="Microsoft YaHei",
                 labelBackgroundColor="#ffffff")
    style.update(zip(("exitX", "exitY", "entryX", "entryY"), map(str, ports)))
    _write_style(edge, names, style)
    geometry = edge.find("mxGeometry")
    if geometry is None:
        geometry = ET.SubElement(edge, "mxGeometry", {"relative": "1", "as": "geometry"})
    points = geometry.find("Array[@as='points']")
    if points is not None:
        geometry.remove(points)
    if waypoints or fallback:
        points = ET.SubElement(geometry, "Array", {"as": "points"})
        for x, y in waypoints or fallback:
            ET.SubElement(points, "mxPoint", x=str(x), y=str(y))


def _add_groups(root, nodes: dict[str, _Node], layout) -> None:
    raw = layout.get("分组")
    used = set()
    for group in raw if isinstance(raw, list) else ():
        if not isinstance(group, dict):
            continue
        label, members = group.get("名称"), group.get("节点")
        if not isinstance(label, str) or not label.strip() or not isinstance(members, list):
            continue
        names = {name for name in members if isinstance(name, str) and name in nodes}
        if len(names) < 2 or names & used:
            continue
        boxes = [nodes[name].box for name in names]
        box = (min(item[0] for item in boxes) - 14, min(item[1] for item in boxes) - 36,
               max(item[2] for item in boxes) + 14, max(item[3] for item in boxes) + 16)
        if any(_overlap(box, node.box) for name, node in nodes.items() if name not in names):
            continue
        used.update(names)
        cell = ET.Element("mxCell", id=f"disclosure-group-{len(used)}", parent="1",
                          value=label, vertex="1", connectable="0",
                          style="rounded=1;fillColor=#f7f9fc;strokeColor=#adbac8;align=left;"
                                "verticalAlign=top;spacingTop=7;spacingLeft=9;fontFamily=Microsoft YaHei;"
                                "fontStyle=1;fontSize=13;fontColor=#334155;")
        x, y, right, bottom = box
        ET.SubElement(cell, "mxGeometry", {"as": "geometry", "x": str(x), "y": str(y),
                                        "width": str(right - x), "height": str(bottom - y)})
        root.insert(2, cell)
