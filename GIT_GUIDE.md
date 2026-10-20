# 项目版本记录与回退

本项目使用本地 Git 保存代码、算法配置、测试、启动脚本和说明文档。初始分支为 `main`，提交作者为 `HouDebang`，使用 GitHub 的 noreply 邮箱，配置仅作用于本项目。

## 已建立的基准

`baseline-20261020`：2026-10-20 开始版本管理时的工作台，包含左侧控制栏、三组算法下拉选择和现有端到端处理流程。初始化版本管理之前的改动没有 Git 提交历史，原来的人工备份仍在 `backups/`。

## 保存一个版本

在项目根目录执行：

```powershell
git status
git add -A
git diff --cached --stat
git commit -m "描述本次修改，例如：调整裂隙检测参数"
```

只有执行 `git commit` 才会建立新的回退点。查看记录：

```powershell
git log --oneline --decorate -12
git tag
```

## 回到基准版本

先提交当前需要保留的改动，再从基准建立一个新分支：

```powershell
git switch -c restore/baseline-20261020 baseline-20261020
```

这会切换代码，保留 `main` 上的后续提交。需要返回最新主分支时执行 `git switch main`。相同恢复分支已存在时，用 `git switch restore/baseline-20261020`。也可以直接告诉 Codex“先保存当前版本，再回到 baseline-20261020”，由 Codex 检查工作区并完成切换。

需要撤销某一个已提交改动而保留完整历史时，可使用 `git revert 提交编号`；发生冲突时先解决冲突，不删除历史提交。

## Git 与工程数据的边界

以下内容保留在本机，未纳入代码仓库：

- `outputs/` 的上传照片、任务状态和运行成果（仅 README 追踪）。
- `backups/` 的历史人工回退备份。
- `examples/` 的真实工程照片及其来源信息（只追踪合成样例和 README）。
- `.venv/`、缓存、日志、模型权重和本地凭据。

因此，回退代码不会清空已有照片和历史成果；任务数据仍需另行备份。代码切换后，工作台可能把与当前代码不一致的成果标记为过期，需要重新运行。不要删除项目中的 `.git/` 目录。

## GitHub 同步

远程仓库为 **Private（私有）**：[HouDebang/tunnel-face-mapping](https://github.com/HouDebang/tunnel-face-mapping)。`origin` 已连接该仓库，初始基准与后续提交可通过同一条主分支同步。本地提交和标签也可离线使用。

保存新版本后执行：

```powershell
git push -u origin main
git push origin --tags
```

GitHub HTTPS 推送已使用 GitHub 官方 CLI 的浏览器授权，凭据存放在 Windows 系统凭据存储中。当前项目的 Git credential helper 调用本机已配置的 CLI，不需要把账号密码填入 Git。不要把密码、令牌写进远程 URL、配置文件或提交记录。项目 `.git/config` 中仅保存作者身份、工具路径和非敏感 Git 设置。

本机浏览器使用 `127.0.0.1:18780` 代理；Git 也已按相同地址配置，仅作用于本项目的 GitHub HTTPS 连接。代理端口变化时更新此设置；不再需要代理时删除该项：

```powershell
git config --local --unset http.https://github.com/.proxy
```

克隆到其他电脑时，需要在那台电脑重新进行 GitHub CLI 或 Git Credential Manager 授权，本机凭据不会随代码上传。

如果普通终端提示找不到 `git`，本机 Codex 已附带 Git，可在 PowerShell 临时加入当前窗口的 PATH：

```powershell
$gitBin = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/native/git/cmd'
$env:Path = "$gitBin;$env:Path"
git --version
```

官方认证说明：[GitHub 身份认证](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/about-authentication-to-github)。
