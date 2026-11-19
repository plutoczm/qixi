# 寻栖本机脚本

| 脚本 | 用途 |
| --- | --- |
| `tray_server.py` | 桌面 EXE 管理的本机服务。日常使用桌面入口或 `start.cmd`。 |
| `dev.py` | 手动调试、环境核对或离线演示。 |
| `install_tray_launcher.ps1` | 在项目移动或启动器变更后重建桌面入口。 |
| `preflight.py` | 离线预核对，供应商注册表在本目录。 |
| `export_web_contract.py` | 检查或生成前端 API 类型。 |
| `check_architecture.py` 与 `check_docs.py` | 离线检查工程架构、契约、文档和串行阶段。 |
| `personal_backup.py` | 按需备份及隔离恢复，保留现用数据。 |
| `clean_generated.py` | 预览可重建文件。明确指定后才删除缓存。 |

真实供应商校验须另有获准样本和原始 HTTP 上限。版本化功能规则见 [规格](../docs/spec.md)，当前状态见 [进度](../docs/progress.md)。
## 按用途查找

- 日常运行使用项目根的 `start.cmd` 和 `stop.cmd`。手动开发或离线演示使用 `dev.py`。
- 维护检查使用 `check_*.py`、`export_web_contract.py` 和 `preflight.py`。
- 单次供应商探针集中在本目录的 `probe_*.py`、`verify_*.py`，评测辅助逻辑在 `evaluation/`。这些都需要独立的执行条件，应用启动流程不会调用。
