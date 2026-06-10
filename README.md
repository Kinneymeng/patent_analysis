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
