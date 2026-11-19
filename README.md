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

以下步骤适用于 Windows 上从源码运行。先用离线演示确认安装成功，再按需配置实际服务。

### 1. 下载和准备

1. [下载寻栖源码 ZIP](https://github.com/plutoczm/qixi/archive/refs/heads/main.zip) 并解压，找到包含 `README.md` 和 `environment.yml` 的项目目录。已有 Git 也可以运行 `git clone https://github.com/plutoczm/qixi.git`。
2. 安装 Conda 和 Node.js 24.15 或更新的 24.x 版本，然后打开 Anaconda Prompt。[Miniconda 下载](https://www.anaconda.com/download) · [Node.js 24 Windows 安装包](https://nodejs.org/download/release/latest-v24.x/)
3. 用 `cd /d "你的项目目录"` 进入解压目录。在这个窗口按顺序执行下面的命令。已有 `xunqi` 环境时，从激活命令开始。环境所需的 Python 3.12 由 Conda 安装。

```bat
conda env create -f environment.yml
conda activate xunqi
python -m pip install -r requirements.lock.txt
npm.cmd ci --prefix frontend
```

### 2. 启动离线演示

```bat
python -X utf8 scripts/dev.py --build --demo
```

在浏览器打开 [本地演示](http://127.0.0.1:8000/)，以「广州」为目的地试查模拟酒店。演示无需 API Key 或飞猪 CLI。终端中按 `Ctrl+C` 停止服务。若端口 8000 已占用，在启动命令末尾加 `--port 8001`，并改用浏览器地址 `http://127.0.0.1:8001/`。

## 接入实际服务

退出离线演示后，在项目目录运行 `copy .env.example .env`。已有 `.env` 时直接编辑。用文本编辑器打开 `.env`，只填自己已获授权且打算使用的服务，其他项留空。模板见 [`.env.example`](.env.example)。

| 想用的功能 | `.env` 字段 |
| --- | --- |
| 道旅酒店与房型参考 | `ROLLINGGO_API_KEY`。可从 [道旅接入文档](https://global.rollinggo.store/docs/mcp-docs/quick-start/) 找到申请入口。 |
| 途牛酒店与房型参考 | `TUNIU_API_KEY`，默认使用模板中的 `XUNQI_TUNIU_AUTH_MODE=api_key`。 |
| 飞猪酒店搜索线索 | `FLYAI_API_KEY`，并在本项目单独安装可选 CLI。 |
| 高德或腾讯的路线 | 分别填写 `AMAP_API_KEY` 或 `TENCENT_MAP_KEY`。腾讯签名启用时填 `TENCENT_MAP_SECRET`。 |
| 滴滴当前车费估价 | `DIDI_MCP_KEY`。 |
| DeepSeek 辅助整理条件 | `DEEPSEEK_API_KEY`。 |

已完成离线演示构建时，运行以下命令切换到实际服务。首次构建可加 `--build`。

```bat
conda activate xunqi
python -X utf8 scripts/dev.py
```

浏览器仍访问 [本地应用](http://127.0.0.1:8000/)。查价和查交通在网页中按需触发。只有道旅 Key 也可以开始查酒店，地图、车费、模型和其他住宿来源按需配置。`.env` 和产生的运行数据保存在本机。

### 可选 · 飞猪 CLI

只有使用飞猪酒店线索才需要此步骤。在项目根目录运行以下命令，将项目已验证的 CLI 版本装到应用读取的位置。Key 仍通过项目根目录的 `.env` 配置。

```bat
if not exist runtime\vendor\flyai-cli mkdir runtime\vendor\flyai-cli
npm.cmd install --prefix runtime/vendor/flyai-cli --no-save --no-package-lock @fly-ai/flyai-cli@1.0.16
```

[飞猪 AI 开放平台](https://flyai.open.fliggy.com/docs/quickstart) · [CLI 软件包](https://www.npmjs.com/package/@fly-ai/flyai-cli/v/1.0.16)

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

[项目规格](docs/spec.md) · [计划基线](docs/plan_baseline.md) · [运行计划](docs/plan_runtime.md) · [当前进度](docs/progress.md)

`$grill-me` 是项目级显式调用技能，基于 [Matt Pocock 的独立版](https://github.com/mattpocock/skills/blob/62f43a18177be6ec82da242e59ffbc490a4c22ea/skills/productivity/grill-me/SKILL.md)，随附 MIT 许可证。
