授权码（区分大小写，请保存）：

- A 一年授权：`433KRSQ3GWEJMUKNFGMX5GUT`
- B 永久授权：`8224Y375KVG9PVM7WDQ5ECRE`

使用方式：

```powershell
PatentSummarizer.exe --repair "433KRSQ3GWEJMUKNFGMX5GUT"
```

或永久激活：

```powershell
PatentSummarizer.exe --repair "8224Y375KVG9PVM7WDQ5ECRE"
```

实现内容：

- 新增[授权模块](D:/03_Proj/02_AI兼职/03_projects/07_专利分类/patent_summarizer/licensing.py)。
- 在[GUI 启动入口](D:/03_Proj/02_AI兼职/03_projects/07_专利分类/patent_summarizer/gui.py:999)创建窗口前检查授权。
- 状态保存在 `%LOCALAPPDATA%\PatentSummarizer\runtime.dat`。
- 到期、密码错误和修复命令均完全静默。
- 源码只保存授权码哈希，不保存上述明文密码。
- 新增[授权测试](D:/03_Proj/02_AI兼职/03_projects/07_专利分类/tests/test_licensing.py)。