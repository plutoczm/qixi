<div align="center">
  <h1>寻栖</h1>
  <p><strong>按目的地找住处，从参考房价看到返程交通。</strong></p>
  <p>Windows 本机应用 · 中国大陆酒店参考查询</p>
</div>

![寻栖首页，左侧是住宿搜索入口，右侧是暖色山水插画](assets/readme/home.png)

[界面预览](#界面预览) · [快速开始](#快速开始) · [接入实际服务](#接入实际服务) · [开发与验证](#开发与验证)

## 界面预览

### 按条件找到值得先看的酒店

输入城市、地标或具体门店，设置日期、人数和预算。道旅、途牛和飞猪结果按来源独立展示，推荐列出优先核对的理由。支持的来源可展开房型，核对入住人数、费用和取消规则。

![三家住宿来源的演示候选及各自的推荐理由](assets/readme/recommendations.png)

### 在酒店卡片上查返程交通

点击「查交通」，核对出发点和酒店后查询。符合当次地点和时效条件时，卡片并列显示步行时间、驾车时间和滴滴当前估价。驾车时间不含候车。

![演示酒店卡片显示步行约 8 分钟、驾车约 6 分钟、快车估价 12 至 18 元](assets/readme/transport.png)

> 截图来自当前应用界面，酒店、地址、价格和交通数据均为演示。实际查询提供参考信息，最终房态、价格与费用以预订来源为准。

## 快速开始

### 从源码启动离线演示

准备 Conda、Python 3.12 和 Node.js 24.15–24.x。有现成的 `xunqi` 环境可从激活环境开始。

```powershell
conda env create -f environment.yml
conda activate xunqi
python -m pip install -r requirements.lock.txt
npm.cmd ci --prefix frontend
python -X utf8 scripts/dev.py --build --demo
```

打开 [本地演示](http://127.0.0.1:8000/)。离线演示无需 API Key，页面会标注合成数据。若 8000 已被本机托盘服务占用，可加 `--port 8001`。

### Windows 桌面入口

当前源码版可通过已配置的桌面 `寻栖.exe` 或根目录 `start.cmd` 启动。现有 EXE 依赖构建时指定的项目目录、本机 Python 环境和前端构建。移动项目或修改启动器后，先退出托盘，再按 [启动脚本说明](scripts/README.md) 重建桌面入口。关闭窗口后后台仍驻留托盘，可从托盘菜单或运行 `stop.cmd` 退出。

## 接入实际服务

将 [`.env.example`](.env.example) 复制为项目根目录的 `.env`，按需填写已获授权的住宿、地图、车费和模型服务凭据。飞猪官方 CLI 按配置说明单独准备。查价和查交通由用户触发，DeepSeek 可辅助整理入住需求。凭据、SQLite 数据、下载的供应商工具、构建产物和日志保存在本机。

## 开发与验证

| 路径 | 内容 |
| --- | --- |
| [frontend/src](frontend/src/) | React、TypeScript 网页，酒店卡片、推荐与交通界面 |
| [backend/stay_agent](backend/stay_agent/) | FastAPI、SQLite 会话、规则和供应商适配器 |
| [launcher](launcher/) · [scripts](scripts/README.md) | Windows 启动器、托盘和本机维护 |
| [tests](tests/) · [frontend/e2e](frontend/e2e/) | 后端回归与浏览器测试 |
| [docker](docker/) · [tools](tools/) | 可选离线容器入口、辅助工具与运行桥接 |

```powershell
python -m pytest -q
npm.cmd test --prefix frontend
npm.cmd run build --prefix frontend
python scripts/export_web_contract.py --check
python scripts/check_architecture.py
python scripts/check_docs.py
```

[项目规格](docs/spec.md) · [计划基线](docs/plan_baseline.md) · [运行计划](docs/plan_runtime.md) · [当前进度](docs/progress.md) · [实习准备笔记](docs/project_notes.md)

`$grill-me` 是项目级显式调用技能，基于 [Matt Pocock 的独立版](https://github.com/mattpocock/skills/blob/62f43a18177be6ec82da242e59ffbc490a4c22ea/skills/productivity/grill-me/SKILL.md)，随附 MIT 许可证。
