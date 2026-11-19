# 寻栖 · 本机个人酒店参考查询

寻栖为中国大陆酒店提供真实候选、原来源房型参考和按需交通查询。Windows 日常入口是桌面 `寻栖.exe` 或项目根 `start.cmd`。关闭应用窗口后后台仍驻留托盘，可用托盘菜单或 `stop.cmd` 退出。

## 目录

| 路径 | 内容 |
| --- | --- |
| [frontend/src](frontend/src/) | React、TypeScript 网页及酒店卡片、推荐和交通界面 |
| [backend/stay_agent](backend/stay_agent/) | FastAPI、SQLite 会话、确定性规则和供应商适配器 |
| [launcher](launcher/) | Windows 桌面入口和托盘控制器 |
| [scripts](scripts/README.md) | 本机启动、构建前检查、保养和诊断入口 |
| [tests](tests/) 与 [frontend/e2e](frontend/e2e/) | 后端回归和浏览器验证 |
| [docker](docker/) | 可选的本机离线容器入口 |
| [tools](tools/) | 项目辅助工具和可选运行桥接 |
| [docs](docs/) | 规格、计划、进度及实习准备笔记 |

本机配置从 [.env.example](.env.example) 起步。填入凭据后的 `.env`、SQLite、下载的供应商工具、构建产物和日志由本机保管。

## 从源码启动离线演示

需要 Conda、Python 3.12 和 Node.js 24.15–24.x。已有 `xunqi` 环境时，从激活环境开始。

```powershell
conda env create -f environment.yml
conda activate xunqi
python -m pip install -r requirements.lock.txt
npm.cmd ci --prefix frontend
python -X utf8 scripts/dev.py --build --demo
```

浏览器打开 `http://127.0.0.1:8000/`。页面会标注合成数据。若 8000 已被托盘服务占用，可加 `--port 8001` 运行独立演示。

接入实际住宿、路线或模型服务时，先有合法授权，再在本机 `.env` 配置相应字段。桌面启动器使用本机的 Python 环境和前端构建。移动项目或改动启动器后，退出托盘，再通过 `scripts/install_tray_launcher.ps1` 重建桌面入口。

## 本地验证

```powershell
python -m pytest -q
npm.cmd test --prefix frontend
npm.cmd run build --prefix frontend
python scripts/export_web_contract.py --check
python scripts/check_architecture.py
python scripts/check_docs.py
```

验收范围见 [项目规格](docs/spec.md)，步骤见 [运行计划](docs/plan_runtime.md)，当前执行位置见 [进度](docs/progress.md)。[计划基线](docs/plan_baseline.md)用于对照，[实习准备笔记](docs/project_notes.md)记录项目中的问题、取舍和验证。

`$grill-me` 是项目级显式调用技能，基于 [Matt Pocock 的独立版](https://github.com/mattpocock/skills/blob/62f43a18177be6ec82da242e59ffbc490a4c22ea/skills/productivity/grill-me/SKILL.md)，随附 MIT 许可证。
