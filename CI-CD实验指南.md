# GitHub Actions CI/CD 实验指南

这份指南帮助你把当前项目上传到 GitHub，并亲手观察一次完整的 CI/CD 流程。你不需要先掌握复杂的 DevOps 知识，按照步骤操作即可。

## 一、什么是 CI/CD

### CI：持续集成

CI（Continuous Integration，持续集成）可以理解为“自动质量检查”。

当你向 GitHub 提交代码或创建 Pull Request 后，GitHub 会临时准备一台干净电脑，自动执行以下动作：

1. 下载代码。
2. 安装 Python 和项目依赖。
3. 运行全部单元测试。
4. 告诉你本次代码是否通过检查。

本项目的 CI 会组合测试：

- Ubuntu 和 Windows。
- Python 3.10 和 Python 3.13。

因此一次 CI 会出现四个测试任务。全部显示绿色对勾，表示当前代码在这些环境中都通过了测试。

### CD：持续交付

CD 在不同项目中可能表示“持续交付”或“持续部署”。

本项目是 Windows 桌面程序，没有服务器需要自动部署，所以这里采用“持续交付”：

1. 自动运行测试。
2. 在 Windows 环境中自动构建 EXE。
3. 把完整便携程序压缩成 ZIP。
4. 保存 ZIP 供下载。
5. 创建版本标签时，自动发布到 GitHub Releases。

简单说：CI 回答“代码有没有问题”，CD 负责“把通过测试的代码变成可交付软件”。

## 二、项目中的两个工作流

工作流文件位于：

```text
.github/workflows/
├─ ci.yml
└─ release.yml
```

### `ci.yml`

以下情况会自动运行：

- 向 `master` 推送代码。
- 创建或更新指向 `master` 的 Pull Request。
- 在 GitHub Actions 页面手动运行。

它只安装依赖和运行测试，不会调用专利分析接口，也不需要 API Key。

### `release.yml`

以下情况会运行：

- 在 GitHub Actions 页面手动点击运行。
- 推送以 `v` 开头的版本标签，例如 `v0.1.0`。

手动运行时会生成一个可下载的 Artifact。推送版本标签时，除了 Artifact，还会创建 GitHub Release 并附上 Windows ZIP。

## 三、第一次上传前的安全检查

### 1. 确认不要上传真实 `.env`

项目的 `.gitignore` 已忽略 `.env`。上传前仍建议运行：

```powershell
git status
```

确认待提交文件中没有 `.env`、真实 API Key、运行日志或真实专利数据。

可以上传 `.env.example`，因为它只应包含配置项示例，不能包含真实密钥。

### 2. 在本机先运行测试

```powershell
python -m unittest discover
```

本机测试通过后再上传，可以减少第一次 CI 失败的概率。

### 3. 确认打包必需文件存在

仓库中应包含：

- `class.xlsx`
- `.env.example`
- `assets/patent-summarizer.ico`
- `使用说明.md`
- `output/doc/汽车底盘领域专利布局分析报告模板.docx`

CI 构建时不会使用你电脑里的真实 `.env`，而是把 `.env.example` 复制为便携包中的 `.env`。下载 ZIP 后，使用者需要在该 `.env` 中填写真实接口配置。

## 四、第一次上传到 GitHub

### 1. 创建 GitHub 仓库

1. 登录 GitHub。
2. 点击右上角 `+`，选择 `New repository`。
3. 输入仓库名称，例如 `patent-summarizer`。
4. 根据需要选择 Public 或 Private。
5. 如果本地项目已经有 README 和 Git 历史，不要勾选自动创建 README、`.gitignore` 或 License。
6. 点击 `Create repository`。

### 2. 关联远程仓库

在项目根目录打开 PowerShell。把下面地址换成你的仓库地址：

```powershell
git remote -v
git remote add origin https://github.com/你的用户名/patent-summarizer.git
```

如果 `git remote -v` 已经显示正确的 `origin`，不要重复执行 `git remote add`。如果已有错误地址，可以使用：

```powershell
git remote set-url origin https://github.com/你的用户名/patent-summarizer.git
```

### 3. 提交并推送

```powershell
git add .
git commit -m "Add GitHub Actions CI and release workflows"
git push -u origin master
```

推送完成后，GitHub 会自动识别 `.github/workflows` 中的工作流。

## 五、查看 CI 实验结果

1. 打开 GitHub 仓库页面。
2. 点击顶部 `Actions`。
3. 左侧选择 `CI`。
4. 点击最新一次运行记录。
5. 你会看到四个测试任务。

状态含义：

- 黄色圆点：正在运行或等待运行。
- 绿色对勾：运行成功。
- 红色叉号：运行失败。
- 灰色图标：任务被跳过或取消。

点击任意任务，再展开具体步骤，就能看到安装依赖和测试输出。如果失败，红色步骤附近通常会显示失败原因。

### 做一个简单观察实验

你可以只修改 README 中的一句话，然后执行：

```powershell
git add README.md
git commit -m "Test CI workflow"
git push
```

再次打开 Actions 页面，会看到新的一次 CI。这个过程就是“每次代码变化都自动重新验证”。

## 六、手动构建 Windows 便携包

`release.yml` 包含 `workflow_dispatch`，因此可以从网页手动运行。该工作流文件必须已经存在于默认分支 `master`。

操作步骤：

1. 打开仓库的 `Actions` 页面。
2. 左侧选择 `Build Windows portable package`。
3. 点击右侧 `Run workflow`。
4. 分支选择 `master`。
5. 再点击绿色的 `Run workflow`。
6. 等待任务完成。
7. 打开本次运行记录，在页面底部的 `Artifacts` 区域下载 `PatentSummarizer-windows-x64`。

下载后解压 ZIP，里面应包含完整的 `PatentSummarizer` 文件夹，而不只是单独一个 EXE。

手动构建只产生 Artifact，不会创建正式 Release。Artifact 在本工作流中保存 14 天，适合测试构建结果。

## 七、发布一个版本

当你认为某个版本可以正式交付时，创建版本标签。建议使用类似 `v0.1.0` 的格式：

- 第一位：不兼容的大版本变化。
- 第二位：新增功能。
- 第三位：问题修复。

第一次实验可以执行：

```powershell
git tag -a v0.1.0 -m "First automated Windows release"
git push origin v0.1.0
```

推送标签后：

1. GitHub 自动运行发布工作流。
2. 自动测试项目。
3. 自动构建 Windows 便携包。
4. 自动生成 ZIP。
5. 自动创建 `v0.1.0` GitHub Release。

完成后打开仓库右侧的 `Releases`，即可看到版本说明和可下载 ZIP。

### 标签写错了怎么办

如果标签已经发布，初学阶段更建议修正代码后发布新标签，例如 `v0.1.1`，不要反复覆盖同一个版本。这样历史记录更清楚。

## 八、Artifact 和 Release 的区别

### Artifact

- 附属于某一次 Actions 运行。
- 适合临时测试和内部下载。
- 会在保留期限结束后自动删除。
- 需要进入 Actions 运行页面下载。

### Release

- 是一个正式版本页面。
- 适合长期保存和对外发布。
- 可以包含版本说明、标签和安装包。
- 用户可以直接从仓库的 Releases 页面下载。

## 九、CI 失败时如何处理

### 依赖安装失败

展开 `Install project and test dependencies`，查看具体哪个包安装失败。偶发网络问题可以点击 `Re-run jobs` 重试。

### 单元测试失败

展开 `Run unit tests`。日志会显示失败的测试名称、文件位置和错误信息。先在本机运行同一命令：

```powershell
python -m unittest discover
```

修复后重新提交并推送，CI 会自动再运行一次。

### Windows 构建失败

查看发布工作流中的 `Build portable application`。常见原因是以下文件没有提交到 GitHub：

- `class.xlsx`
- 报告模板
- 图标
- `使用说明.md`
- `.env.example`

### 看不到“Run workflow”按钮

确认：

- `release.yml` 已经推送到默认分支。
- 默认分支是 `master`。
- 你对仓库有写权限。
- 仓库 Settings → Actions 中没有禁用 Actions。

### Release 创建失败

进入仓库 Settings → Actions → General，检查 Workflow permissions。工作流已经声明 `contents: write`，一般不需要手工创建 Token；GitHub 会自动提供临时的 `GITHUB_TOKEN`。

## 十、建议的后续实验

### 1. 创建 Pull Request

以后如果愿意尝试多分支开发，可以从临时分支创建 PR。PR 页面会直接显示 CI 是否通过。当前只使用 `master` 也不影响本指南中的实验。

### 2. 设置合并保护

在 GitHub 的 Rules 或 Branch protection 中，可以要求 CI 通过后才能合并到 `master`。这是团队项目中常见的质量门槛。

### 3. 观察失败再修复

可以在实验分支中临时写一个必然失败的测试，观察 GitHub 显示红叉；修复后再次推送，观察它变为绿色。不要在正式 `master` 中故意保留失败代码。

## 十一、安全与费用提醒

- 工作流不会调用 OpenAI 兼容接口，不需要上传 API Key。
- 不要把真实 `.env` 加入 Git。
- GitHub 托管运行器会消耗 Actions 分钟数；公开仓库和不同账户方案的额度规则可能不同，请以 GitHub 当前计费页面为准。
- Windows 运行器通常比 Linux 运行器消耗更多额度。若以后希望节省时间，可以减少 CI 测试矩阵。
- 来自陌生贡献者的代码不要直接赋予敏感 Secrets。

## 十二、本实验成功的判断标准

完成以下三项，就说明你已经走通了基础 CI/CD：

1. 推送代码后，`CI` 的四个测试任务全部为绿色。
2. 手动运行发布工作流后，可以下载 Windows ZIP Artifact。
3. 推送 `v0.1.0` 标签后，Releases 页面自动出现对应版本和 ZIP。

