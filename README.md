# TELEMAC-2D MCP

在 Windows 上通过 MCP 检查和运行 TELEMAC-2D 算例，并读取 SERAFIN 结果。当前版本使用本地 stdio 连接，实测环境为 openTELEMAC 9.1.1 和其自带的 Python 3.12。

## 安装与接入

1. 安装 [openTELEMAC Windows 版](https://open-simulation-center.org/fr/downloads/opentelemac/openTELEMAC/9.1.1) 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)。建议使用完整版，以便访问随包算例。TELEMAC 和本仓库都放在无空格的本地 ASCII 路径下。
2. 在本仓库目录运行安装脚本，将示例路径改成实际的 TELEMAC 安装路径。

   ```powershell
   .\setup.ps1 -TelemacRoot 'D:\Tools\TELEMAC\9.1.1'
   ```

3. 在本仓库目录运行以下命令，把服务注册到 Codex。其他 MCP 客户端可使用相同的 stdio 命令和参数。

   ```powershell
   codex mcp add telemac-2d -- cmd.exe /d /c (Join-Path $PWD 'telemac-python.cmd') -B (Join-Path $PWD 'server.py')
   ```

## 工具

| 工具 | 用途 |
| --- | --- |
| `check_telemac` | 查看 Python、TELEMAC 配置和算例根目录 |
| `inspect_case` | 检查相对于算例根目录的 `.cas`，返回时间步、已声明文件及发现的问题 |
| `run_case` | 将受信任的算例提交为后台串行任务，返回运行 ID |
| `get_run_status` | 查询任务状态、日志尾部和结果路径 |
| `summarize_results` | 按帧汇总节点变量。默认读取末帧 |

把算例放在 `cases/`，也可通过 `TELEMAC_CASES_ROOT` 指定绝对目录。调用时使用相对于根目录的 `.cas` 路径。每次任务的输入副本、日志和输出位于 `runs/` 下的独立目录。算例可以提供并运行自定义 Fortran，请仅提交信任的算例。首版支持独立的 TELEMAC-2D 串行计算，结果格式为 SERAFIN 和 SERAFIND。

## 官方算例

将安装目录下 `examples/telemac2d/gouttedo` 复制到仓库的 `cases/`，然后运行 `gouttedo/t2d_gouttedo.cas`。Windows 实测输出为 21 帧、4,624 个节点，末帧 4.0 秒。 [openTELEMAC 官方教程](https://opentelemac.org/en/learning/example)
