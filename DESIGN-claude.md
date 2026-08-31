---
version: beta
name: PatentSummarizer-desktop-editorial
description: A CustomTkinter desktop design system for the Patent DOCX processing EXE. It adapts the warm cream, coral, and dark product-surface language from the original Claude-inspired reference, but removes web marketing patterns, licensed brand assets, and browser-only components so the guidance can be implemented in the current Python desktop application.

app_context:
  product: "专利 DOCX 处理工具"
  runtime: "Python + customtkinter + PyInstaller EXE"
  entry_file: "patent_summarizer/gui.py"
  packaging: "PatentSummarizer.spec and build_portable.py"
  primary_user_goal: "选择专利目录，执行批量总结、分类、趋势分析、对比和报告任务，并查看运行日志。"
  current_ui_note: "当前界面使用 Material purple palette. The target design should replace it with a warmer editorial desktop palette while preserving the existing workflow."

applicability:
  usable_for_exe: true
  summary: "The original DESIGN-claude.md can be used only as an aesthetic reference, not as a direct implementation spec. This revised file is the implementation-oriented version for the current CustomTkinter EXE."
  why_original_needed_changes:
    - "It described a web marketing site: top navigation, hero bands, pricing cards, connector grids, CTA bands, and footer are not part of this desktop EXE."
    - "It referenced Anthropic/Claude brand assets, including the spike mark and licensed typefaces. Those should not be copied into this product."
    - "It used browser/CSS concepts such as 12-column grids, full-width bands, responsive breakpoints, and web font stacks. CustomTkinter supports a simpler widget tree, fixed desktop windows, and Python-side style configuration."
    - "It emphasized large editorial display type. The EXE needs denser operational UI with compact labels, clear task controls, and readable logs."
  retained_design_language:
    - "Warm cream canvas instead of the current pale purple page background."
    - "Muted coral as the primary run/action color."
    - "Dark product surface for the running log area."
    - "Low-shadow, color-block-first layout with subtle hairline borders."
    - "8-12px radius scale for controls and panels."

colors:
  primary: "#CC785C"
  primary_hover: "#B86950"
  primary_pressed: "#A9583E"
  primary_disabled: "#E6DFD8"
  ink: "#141413"
  body: "#3D3D3A"
  body_strong: "#252523"
  muted: "#6C6A64"
  muted_soft: "#8E8B82"
  canvas: "#FAF9F5"
  surface: "#FFFFFF"
  surface_soft: "#F5F0E8"
  surface_card: "#EFE9DE"
  surface_input: "#FFFEFA"
  surface_selected: "#E8E0D2"
  hairline: "#E6DFD8"
  hairline_soft: "#EBE6DF"
  dark: "#181715"
  dark_elevated: "#252320"
  dark_soft: "#1F1E1B"
  on_primary: "#FFFFFF"
  on_dark: "#FAF9F5"
  on_dark_soft: "#B8B2AA"
  success: "#4F9D67"
  success_surface: "#E4F5E9"
  warning: "#B88918"
  warning_surface: "#FFF4D6"
  error: "#C64545"
  error_surface: "#FDE8E7"

typography:
  desktop_family: "Microsoft YaHei UI"
  fallback_family: "Segoe UI"
  monospace_family: "Consolas"
  title:
    family: "Microsoft YaHei UI"
    size: 24
    weight: "bold"
    line_height: 1.25
  subtitle:
    family: "Microsoft YaHei UI"
    size: 13
    weight: "normal"
    line_height: 1.45
  section_label:
    family: "Microsoft YaHei UI"
    size: 13
    weight: "bold"
    line_height: 1.35
  body:
    family: "Microsoft YaHei UI"
    size: 13
    weight: "normal"
    line_height: 1.45
  small:
    family: "Microsoft YaHei UI"
    size: 11
    weight: "normal"
    line_height: 1.35
  button:
    family: "Microsoft YaHei UI"
    size: 13
    weight: "bold"
    line_height: 1
  log:
    family: "Consolas"
    size: 12
    weight: "normal"
    line_height: 1.45

rounded:
  xs: 4
  sm: 6
  md: 8
  lg: 12
  xl: 16
  panel: 18

spacing:
  page_x: 28
  header_top: 24
  header_bottom: 12
  panel_x: 22
  panel_y: 22
  control_gap: 12
  row_gap: 14
  section_gap: 16
  footer_bottom: 8

components:
  app_window:
    widget: "CTk"
    fg_color: "{colors.canvas}"
    geometry: "980x720"
    minsize: "860x620"
    note: "Keep the existing desktop size. Do not convert the EXE into a browser-style landing page."
  header:
    widget: "CTkFrame"
    fg_color: "transparent"
    padding: "28px 24px 28px 12px"
    title_color: "{colors.ink}"
    subtitle_color: "{colors.body}"
    note: "Header is functional and concise: title plus one-line usage hint."
  main_panel:
    widget: "CTkFrame"
    fg_color: "{colors.surface}"
    border_color: "{colors.hairline}"
    border_width: 1
    corner_radius: "{rounded.panel}"
    note: "The main working area stays as one framed panel. Avoid nested card-on-card decoration."
  segmented_task_tabs:
    widget: "CTkFrame + CTkButton"
    container_fg: "{colors.hairline}"
    container_radius: "{rounded.lg}"
    active_fg: "{colors.primary}"
    active_hover: "{colors.primary_hover}"
    active_text: "{colors.on_primary}"
    inactive_fg: "{colors.surface_card}"
    inactive_hover: "{colors.surface_selected}"
    inactive_text: "{colors.ink}"
    button_height: 38
    button_width: 104
    button_radius: 10
    note: "Maps to the current mode buttons: 批量总结, 分类, 趋势分析, 对比, 报告, 技术交底书."
  section_label:
    widget: "CTkLabel"
    text_color: "{colors.body_strong}"
    typography: "{typography.section_label}"
  input:
    widget: "CTkEntry"
    fg_color: "{colors.surface_input}"
    border_color: "{colors.hairline}"
    text_color: "{colors.ink}"
    placeholder_text_color: "{colors.muted_soft}"
    height: 38
    corner_radius: 10
    note: "Use for folder and output path fields."
  button_primary:
    widget: "CTkButton"
    fg_color: "{colors.primary}"
    hover_color: "{colors.primary_hover}"
    text_color: "{colors.on_primary}"
    height: 42
    corner_radius: "{rounded.lg}"
    typography: "{typography.button}"
    note: "Use for 开始运行. This is the dominant CTA."
  button_secondary:
    widget: "CTkButton"
    fg_color: "{colors.dark_elevated}"
    hover_color: "{colors.dark_soft}"
    text_color: "{colors.on_dark}"
    height: 38
    corner_radius: 10
    typography: "{typography.button}"
    note: "Use for 选择 / 另存为. Secondary buttons should be quieter than the run button but still clear."
  button_subtle:
    widget: "CTkButton"
    fg_color: "{colors.surface_card}"
    hover_color: "{colors.surface_selected}"
    text_color: "{colors.ink}"
    height: 36
    corner_radius: 9
    typography: "{typography.button}"
    note: "Use for 添加目录, 移除, and status/detail buttons."
  status_strip:
    widget: "CTkFrame"
    fg_color: "{colors.surface_soft}"
    corner_radius: "{rounded.lg}"
    text_color: "{colors.primary_pressed}"
    note: "Shows selected path, validation message, or ready/running/completed state."
  progress:
    widget: "CTkProgressBar + CTkLabel"
    location: "Between the status/action strip and the log panel."
    track_color: "{colors.hairline}"
    running_color: "{colors.primary}"
    success_color: "{colors.success}"
    error_color: "{colors.error}"
    height: 9
    corner_radius: 5
    note: "Show stage, completed/total work units, and percentage. Use indeterminate motion only while the task scope is genuinely unknown."
  log_panel:
    widget: "CTkFrame"
    fg_color: "{colors.dark}"
    corner_radius: "{rounded.lg}"
    title_color: "{colors.on_dark}"
    note: "The dark area is the product surface. It should feel like an operational console, not a decorative block."
  log_textbox:
    widget: "CTkTextbox"
    fg_color: "{colors.dark_soft}"
    text_color: "{colors.on_dark}"
    scrollbar_button_color: "{colors.dark_elevated}"
    scrollbar_button_hover_color: "{colors.primary_pressed}"
    typography: "{typography.log}"
    corner_radius: 10
  scrollable_list:
    widget: "CTkScrollableFrame"
    fg_color: "{colors.surface_soft}"
    scrollbar_button_color: "{colors.hairline}"
    corner_radius: "{rounded.lg}"
  copyright:
    widget: "CTkLabel"
    text_color: "{colors.muted}"
    typography: "{typography.small}"

layout:
  window:
    columns: "single flexible column"
    rows: "header, main panel, copyright"
    background: "{colors.canvas}"
  main_panel:
    rows:
      - "task selector row"
      - "mode-specific configuration"
      - "status/action strip"
      - "progress strip"
      - "log panel, grows to fill remaining height"
  density:
    rule: "This is an operational desktop tool. Keep controls compact, aligned, and scannable."
    avoid:
      - "Large web hero sections"
      - "Pricing, marketing, testimonial, and footer components"
      - "Oversized serif headlines"
      - "Decorative illustration cards"
  grid_alignment:
    labels: "Left column labels should align visually across rows."
    fields: "Path inputs take remaining width."
    buttons: "Browse/action buttons use fixed widths."
    log: "Log panel must keep the largest vertical share."

state_rules:
  ready:
    status_text_color: "{colors.primary_pressed}"
    status_surface: "{colors.surface_soft}"
  running:
    primary_button_state: "disabled"
    mode_buttons_state: "disabled"
    status_text: "正在运行，请等待..."
    progress: "Use coral determinate progress after work units are known; use coral indeterminate motion while preparing the work plan."
  success:
    status_text_color: "{colors.success}"
    status_surface: "{colors.success_surface}"
    progress: "Keep the final 100% fill and switch the indicator to success green."
  warning:
    status_text_color: "{colors.warning}"
    status_surface: "{colors.warning_surface}"
  error:
    status_text_color: "{colors.error}"
    status_surface: "{colors.error_surface}"
    progress: "Keep the last completed value and switch the indicator to error red; never advance to 100% merely because execution stopped."
  disabled:
    fg_color: "{colors.primary_disabled}"
    text_color: "{colors.muted}"

implementation_mapping:
  palette:
    current: "GOOGLE_MATERIAL_PALETTE in patent_summarizer/gui.py"
    target: "Replace or rename it to DESKTOP_EDITORIAL_PALETTE using the colors in this file."
  theme:
    current: "MODERN_COLOR_THEME = \"green\""
    target: "Use CustomTkinter's light appearance mode, then override component colors explicitly with this palette."
  fonts:
    current: "Microsoft YaHei UI for labels/buttons and Consolas for log"
    target: "Keep these fonts. Do not depend on licensed Claude/Anthropic fonts."
  buttons:
    current: "_button(), mode_button_style(), start_button"
    target: "Map selected tabs to primary, browse buttons to dark secondary, subtle actions to cream buttons."
  log:
    current: "Purple-black log panel"
    target: "Use {colors.dark} and {colors.dark_soft}."
  copyright:
    current: "Bottom centered copyright label"
    target: "Keep it. Use muted ink on canvas."

do:
  - "Use warm cream canvas for the whole EXE background."
  - "Use coral only for the primary task selection state, start button, focus/status emphasis, and selected/high-value moments."
  - "Use the dark surface only for logs or command-like output."
  - "Keep the current five workflow modes and existing validation behavior."
  - "Keep all controls readable at 860x620 minimum window size."
  - "Use CustomTkinter-supported properties: fg_color, hover_color, text_color, border_color, border_width, corner_radius, height, width, font."
  - "Prefer explicit palette references over inline hex values in GUI code."

dont:
  - "Do not use Anthropic/Claude logo marks, names, or brand glyphs."
  - "Do not use Copernicus, StyreneB, or other licensed brand fonts as implementation requirements."
  - "Do not keep the original Material purple palette."
  - "Do not introduce a website-style hero, navbar, pricing card, connector gallery, or marketing footer."
  - "Do not make the interface one-note purple, blue, beige, or dark slate. The intended balance is cream canvas, white panel, coral action, dark log."
  - "Do not add decorative imagery unless it directly helps the task workflow."

future_implementation_checklist:
  - "Replace GOOGLE_MATERIAL_PALETTE with the revised palette."
  - "Update mode_button_style() to use coral active state and cream inactive state."
  - "Update root window fg_color from surface_dim to canvas."
  - "Update main panel border and surface colors."
  - "Change the start button from tertiary purple to primary coral."
  - "Change browse/secondary buttons to dark elevated or subtle cream depending on importance."
  - "Update log panel to dark/dark_soft colors."
  - "Run python -m unittest discover after code changes."
  - "Launch the GUI or rebuilt EXE and visually check the 980x720 and 860x620 layouts."

notes:
  - "This file is a design implementation guide. It does not change the EXE by itself; the GUI implementation lives in patent_summarizer/gui.py."
  - "The design can be implemented without searching GitHub because the current project already has a clear CustomTkinter UI and the revised tokens map directly to its components."
---

# 专利 DOCX 处理工具桌面设计指南

## 结论

原始 `DESIGN-claude.md` 不适合原样用于当前 EXE。它的配色方向可以保留，但它描述的是 Claude/Anthropic 的网页营销设计系统，不是 Python 桌面工具的组件规范。

当前项目的 EXE 入口是 `patent_summarizer/gui.py`，使用 `customtkinter` 构建，再通过 PyInstaller 打包。因此，本文件已经改写为桌面应用可执行的设计指导：保留暖米色画布、珊瑚色主按钮、深色日志面板和低阴影质感，删除 Claude 品牌资产、网页 hero、导航、pricing、footer、响应式网页栅格等不适用内容。

## 设计目标

界面应像一个安静、可靠、偏专业的桌面处理台。用户打开 EXE 后，第一眼看到的是任务类型、目录选择、运行状态和日志，而不是营销式介绍。

目标气质：

- 温暖但克制，避免当前紫色 Material 风格带来的强装饰感。
- 操作优先，目录、任务、状态、日志层级清楚。
- 主行动明确，`开始运行` 是唯一最高优先级按钮。
- 日志区像真实控制台，深色但不压迫。

## 当前界面适配方式

当前界面已经有可复用结构：

- 顶部标题和说明。
- 一个主面板。
- 六个任务 tab。
- 模式相关配置区。
- 状态条和开始按钮。
- 深色运行日志。
- 底部版权文字。

所以无需重建界面结构。建议只替换视觉 token 和少量组件样式，让现有 CustomTkinter 控件按本文件的 palette 和 component mapping 呈现。

## 不需要 GitHub 替代方案

目前不需要去 GitHub 搜索开源 UI 套件。原因是：

- 当前项目已经使用 `customtkinter`，直接换一套 Web/Qt/Tauri 组件库会显著增加重构成本。
- 原设计文件的核心视觉语言可以安全迁移，只要剥离 Claude 品牌和网页营销模式。
- 本文件已经给出与 `CTkFrame`、`CTkButton`、`CTkEntry`、`CTkTextbox`、`CTkScrollableFrame` 的一一映射。

只有当后续决定放弃 CustomTkinter、改成 Qt、Tauri、Electron 或 WebView 壳时，才建议重新搜索更完整的开源桌面 UI 方案。
