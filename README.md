# Patent Summarizer

批量处理中国专利格式的 PDF/DOCX 文档，抽取文本后调用 OpenAI 协议兼容 API，生成同名 Markdown 摘要。

## 安装依赖

核心 CLI 只使用 Python 标准库。实际处理文档时建议安装：

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
- `_internal\`：EXE 运行依赖，包含 CustomTkinter、Python 运行库和 Pandoc 等文件。

使用时把整个 `dist\PatentSummarizer\` 文件夹复制给使用者，不要只复制 EXE。打开 EXE 后选择专利目录，再选择“批量总结”“分类”或“趋势分析”运行。

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
- `patent_summarizer\`、`tests\`、`pyproject.toml`、`PatentSummarizer.spec`、`build_portable.py`：源码、测试和打包配置。

安全提醒：`.env` 可能包含 API Key，不要把真实密钥提交到 Git，也不要发给不该接收的人。命令行清理时不要使用 `del /s`、`rd /s`、`rmdir /s` 等批量删除命令。
