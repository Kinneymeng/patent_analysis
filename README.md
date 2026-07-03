# Patent Summarizer

批量处理中国专利格式的 PDF/DOCX 文档，抽取文本后调用 OpenAI 协议兼容 API，生成同名 Markdown 摘要。

## 安装依赖

先安装项目本身。分类、趋势分析和 DOCX 报告所需的 `openpyxl`、`python-docx` 会一并安装：

```powershell
pip install -e .
```

实际处理 PDF 或 OCR 时建议再安装：

```powershell
pip install pdfplumber pypdf pdf2image pytesseract
```

DOCX 抽取依赖 Pandoc；OCR 需要本机安装 Tesseract 和 Poppler。

## 配置

复制 `.env.example` 为 `.env`，填写：

```env
OPENAI_BASE_URL=https://your-openai-compatible-endpoint/v1
OPENAI_API_KEY=your_key_here
OPENAI_MODEL=your_model_id
PATENT_API_RPM=10
PATENT_MAX_RETRIES=5
PATENT_OCR_ENABLED=true
```

`PATENT_API_RPM=10` 会让客户端全局控制请求速度，默认每次请求之间至少间隔约 6.5 秒，并对 `408/409/429/5xx`、连接错误和超时执行重试。

## 使用

```powershell
python -m patent_summarizer run --input "D:\path\to\patents" --recursive
```

常用参数：

- `--dry-run`：只列出待处理文件并估算最少 API 请求数。
- `--limit N`：只处理前 N 个文件，适合小批量试跑。
- `--overwrite`：覆盖已有同名 Markdown；默认跳过。
- `--api-rpm N`：覆盖 `.env` 中的请求上限。

每个 `a.pdf` 或 `a.docx` 会在原文件旁生成 `a.md`。运行日志写到输入目录下的 `.patent_summary_logs/run.jsonl`，失败清单写到 `.patent_summary_logs/failures.csv`。

## 分类到 Excel

准备两列分类表，第一列为一级分类，第二列为二级分类，例如本项目的 `class.xlsx`。运行：

```powershell
python -m patent_summarizer classify --input "D:\path\to\patents" --classes class.xlsx --output patent_classification.xlsx --recursive
```

分类流程会优先复用同名 `.md` 摘要；若摘要不存在，会先生成摘要再分类。结果 Excel 每篇专利一行，包含主分类、候选分类、置信度、复核状态、分类理由、著录项字段以及摘要字段。中途结果写入输入目录下的 `.patent_summary_logs/classifications.jsonl`，再次运行时会复用已完成记录。

如需让旧摘要重新抽取申请号、申请日、公开日、申请人、发明人、IPC/CPC 等著录信息，可增加：

```powershell
python -m patent_summarizer classify --input "D:\path\to\patents" --classes class.xlsx --output patent_classification.xlsx --recursive --refresh-summaries
```
- `--refresh-summaries`：刷新所有摘要，包括已分类的。

## 标签趋势分析

分类完成后，可按“主二级分类”聚合专利并生成趋势分析报告：

```powershell
python -m patent_summarizer analyze --classification patent_classification.xlsx --output patent_trends.xlsx
```

趋势分析会读取分类结果 Excel，按标签统计专利数、年度分布、置信度、复核数量、热点关键词，并调用同一 OpenAI 兼容接口生成每个标签的发展现状、发展阶段、主要技术路线、热点方向、机会与空白、风险和证据局限。输出工作簿包含 `标签总览`、`年度趋势`、`趋势报告`、`专利明细` 四个 Sheet。

常用参数：

- `--dry-run`：只列出待分析标签，不调用 API。
- `--batch-size N`：单个标签下每批送入模型的专利数量，默认 40。
- `--api-rpm N`：覆盖 `.env` 中的请求上限。

## A vs 外部专利组合对比

如果已经为 A/B/C/D 等专利目录生成同名 Markdown 摘要，可用 `compare` 对比 A 与一个或多个外部组合：

```powershell
python -m patent_summarizer compare `
  --target "D:\path\to\A" `
  --target-label A `
  --baseline "B=D:\path\to\B" `
  --baseline "C=D:\path\to\C" `
  --classes class.xlsx `
  --output "D:\path\to\a_vs_external.xlsx" `
  --recursive
```

每个目录会优先读取目录下的 `patent_classification.xlsx`。如果不存在，会扫描 `.md` 摘要并使用 `--classes` 指定的分类表自动生成该目录的 `patent_classification.xlsx` 后再对比；如需强制重建分类结果，可加 `--refresh-classification`。当提供多个 `--baseline` 时，报告会同时包含 A vs 每个外部目录，以及 A vs `外部总体`。

运行前会递归检查每个目标/外部目录中的 DOCX：每个 `专利.docx` 必须在同一目录存在 `专利.md`，且目录中至少包含一篇 DOCX。任一目录不完整时命令会停止，并提示先对所有专利目录执行 `run`/“批量总结”。

输出工作簿包含 `对比总览`、`标签对比`、`布局建议`、`年度对比`、`专利明细`、`数据来源` 六个 Sheet，用于查看 A 的布局概况、相对优势/短板、差异化程度、是否贴近外部主流布局，以及按优先级排序的建议布局点。

常用参数：

- `--baseline LABEL=PATH`：可重复传入多个外部组合；不写 `LABEL=` 时默认使用文件夹名。
- `--dry-run`：只列出各目录的分类来源、数量和计划对比对象，不调用 API。
- `--classes PATH`：当某个目录缺少 `patent_classification.xlsx` 或使用 `--refresh-classification` 时必填。
- `--api-rpm N`：覆盖 `.env` 中的请求上限。

## 生成 DOCX 专利布局报告

`report` 从各企业目录中的结构化 Markdown 摘要开始，自动复用分类结果、生成趋势分析和组合对比，并在现有 DOCX 模板基础上生成正式的专利布局分析报告。CLI 和便携 GUI 均可使用该功能。

### 前提条件

1. 使用 Python 3.10 或更高版本并执行 `pip install -e .`。
2. 已在 `.env` 中配置可用的 OpenAI 协议兼容接口。
3. 已准备两列分类表，例如项目根目录下的 `class.xlsx`。
4. 长安汽车和每家竞争对手必须分别存放在独立目录中。
5. 每个企业目录中应已经通过 `run` 生成同名 `.md` 摘要。报告阶段以 MD 和分类结果为证据，不重新读取原始专利全文。
6. DOCX 模板必须存在，默认路径为 `output\doc\汽车底盘领域专利布局分析报告模板.docx`。
7. 目标企业和各竞争对手的显示标签不能重复。

建议的目录结构：

```text
D:\底盘专利\
├─ 长安汽车\
│  ├─ 专利A.docx
│  └─ 专利A.md
├─ 理想汽车\
│  ├─ 专利B.docx
│  └─ 专利B.md
├─ 比亚迪\
└─ 吉利汽车\
```

### 第一步：为各企业生成 Markdown 摘要

对每个企业目录分别执行：

```powershell
python -m patent_summarizer run --input "D:\底盘专利\长安汽车" --recursive
python -m patent_summarizer run --input "D:\底盘专利\理想汽车" --recursive
python -m patent_summarizer run --input "D:\底盘专利\比亚迪" --recursive
python -m patent_summarizer run --input "D:\底盘专利\吉利汽车" --recursive
```

### 第二步：执行报告预检查

预检查不会调用 API，也不会生成分类表或 DOCX：

```powershell
python -m patent_summarizer report --dry-run `
  --target "D:\底盘专利\长安汽车" `
  --target-label "长安汽车" `
  --baseline "理想汽车=D:\底盘专利\理想汽车" `
  --baseline "比亚迪=D:\底盘专利\比亚迪" `
  --baseline "吉利汽车=D:\底盘专利\吉利汽车" `
  --classes "class.xlsx" `
  --template "output\doc\汽车底盘领域专利布局分析报告模板.docx" `
  --output "output\doc\汽车底盘领域专利布局分析报告.docx" `
  --recursive
```

### 第三步：生成正式 DOCX

去掉 `--dry-run` 后运行相同命令：

```powershell
python -m patent_summarizer report `
  --target "D:\底盘专利\长安汽车" `
  --target-label "长安汽车" `
  --baseline "理想汽车=D:\底盘专利\理想汽车" `
  --baseline "比亚迪=D:\底盘专利\比亚迪" `
  --baseline "吉利汽车=D:\底盘专利\吉利汽车" `
  --classes "class.xlsx" `
  --template "output\doc\汽车底盘领域专利布局分析报告模板.docx" `
  --output "output\doc\汽车底盘领域专利布局分析报告.docx" `
  --recursive
```

每个企业目录会优先读取已有的 `patent_classification.xlsx`。如果不存在，程序会扫描 `.md`，使用 `--classes` 指定的分类表自动生成该目录的 `patent_classification.xlsx`。当 MD 已更新且需要重建分类时，增加 `--refresh-classification`。

报告默认拒绝覆盖已有 DOCX；确认需要重建时增加 `--overwrite`。分析检查点保存在输出目录下的 `.patent_summary_logs`，相同输入和提示词再次运行时会复用已完成的模型响应。

报告包含样本行业布局、各竞争对手布局与策略、竞争趋势研判、长安汽车优劣势、长安布局策略、待布局点、组合专利策略和证据附录。报告中的“行业”只代表本次上传并完成摘要、分类的样本范围，不等同于完整行业数据库结论。

常用参数：

- `--baseline LABEL=PATH`：可重复指定竞争对手。
- `--batch-size N`：控制每批趋势分析的专利数量，默认 40。
- `--refresh-classification`：忽略已有分类工作簿，从 MD 重建。
- `--overwrite`：覆盖已有输出 DOCX。
- `--dry-run`：仅检查输入和计划。
- `--api-rpm N`：覆盖 `.env` 中的请求上限。

## 便携 GUI EXE

Windows 便携版只处理 DOCX 文件，不处理 PDF/OCR。GUI 使用 CustomTkinter，并内置 `pypandoc-binary` 提供的 Pandoc。

### 自行打包

在项目根目录运行以下命令：

```powershell
conda run -n temp python -m pip install -e .
conda run -n temp python -m pip install pypandoc-binary pyinstaller customtkinter
conda run -n temp python build_portable.py
```

构建结果位于 `dist\PatentSummarizer\`，包含：

- `PatentSummarizer.exe`：主程序。
- `.env`：API 配置。若项目根目录没有 `.env`，构建脚本会复制 `.env.example` 作为模板。
- `class.xlsx`：分类表。
- `汽车底盘领域专利布局分析报告模板.docx`：报告功能使用的内置模板。
- `patent-summarizer.ico`：程序窗口和便携包使用的自定义图标。
- `使用说明.md`：面向普通使用人员的完整操作指南。
- `_internal\`：EXE 运行依赖，包含 CustomTkinter、Python 运行库和 Pandoc 等文件。

使用时把整个 `dist\PatentSummarizer\` 文件夹复制给使用者，不要只复制 EXE。GUI 提供五项任务：

- “批量总结”“分类”“趋势分析”继续使用单个专利目录。
- “对比”和“报告”使用一个目标组合及一个或多个对比组合。名称默认取文件夹名，也可以改成报告中使用的正式名称；所有名称必须非空且不能重复。
- GUI 会递归检查每个目录的 DOCX/MD 配对并显示摘要状态。目录没有 DOCX 或缺少任意同名 MD 时不能启动，需先分别对这些目录运行“批量总结”。
- 每个组合目录中的 `patent_classification.xlsx` 相互独立；已有文件会被复用，缺失时使用 EXE 同目录的 `class.xlsx` 自动生成。
- “对比”默认输出 `<目标名称>_专利组合对比.xlsx`，“报告”默认输出 `<目标名称>_专利布局分析报告.docx`，均保存到目标目录并可通过“另存为”修改。
- “报告”固定使用便携包内置的 `汽车底盘领域专利布局分析报告模板.docx`。

完整的逐步说明、前置条件和常见问题请查看根目录的 [使用说明.md](使用说明.md)。

如果 `dist\PatentSummarizer\` 已存在，`build_portable.py` 会停止，避免覆盖已有便携包。需要重新打包时，先手动备份、改名或清理旧的构建输出，再重新运行打包命令。

### 可清理文件

以下是构建或测试产生的文件夹，确认不再需要后可以删除：

- `build\`
- `build_fixed\`
- `build_modern\`
- `build_palette\`
- `dist_fixed\`
- `dist_modern\`
- `dist_palette\`
- `patent_summarizer.egg-info\`
- `__pycache__\`
- `patent_summarizer\__pycache__\`
- `tests\__pycache__\`

以下不要删除，除非你明确不再需要对应功能：

- `dist\PatentSummarizer\`：最终便携版程序目录。
- `dist\PatentSummarizer\_internal\`：EXE 运行依赖。
- `dist\PatentSummarizer\.env`：EXE 运行配置。
- `dist\PatentSummarizer\class.xlsx`：EXE 分类功能需要的分类表。
- `dist\PatentSummarizer\汽车底盘领域专利布局分析报告模板.docx`：EXE 报告功能需要的内置模板。
- `dist\PatentSummarizer\patent-summarizer.ico`：EXE 的自定义图标文件。
- `dist\PatentSummarizer\使用说明.md`：随便携包交付的用户操作指南。
- `patent_summarizer\`、`tests\`、`pyproject.toml`、`PatentSummarizer.spec`、`build_portable.py`：源码、测试和打包配置。

安全提醒：`.env` 可能包含 API Key，不要把真实密钥提交到 Git，也不要发给不该接收的人。命令行清理时不要使用 `del /s`、`rd /s`、`rmdir /s` 等批量删除命令。

## GitHub Actions CI/CD

仓库包含两条自动化工作流：

- `.github/workflows/ci.yml`：推送到 `master` 或创建 Pull Request 时，在 Ubuntu/Windows 和 Python 3.10/3.13 上运行完整测试。
- `.github/workflows/release.yml`：支持手动构建 Windows 便携 ZIP；推送 `v*` 标签时还会自动创建 GitHub Release。

工作流不会调用专利分析接口，也不需要上传 API Key。CI 构建使用 `.env.example` 生成便携包中的待填写 `.env`。

第一次上传 GitHub、查看 CI、下载 Artifact 和发布 `v0.1.0` 的完整步骤见 [CI-CD实验指南.md](CI-CD实验指南.md)。
