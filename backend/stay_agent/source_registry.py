"""One registry for every data channel this project knows about.

The phase documents describe each source in prose, which makes it easy to say
"we also use Meituan" without recording whether it can actually return a price.
This module is the single machine-readable answer: what each channel is, whether
it is wired up, what it can prove, and what it must never be used for.

Two rules are encoded here rather than left to reviewers:

* **A source's ``kind`` decides where its data may appear.** Only a ``quote``
  source may produce a price card. A ``discovery`` source may suggest a hotel
  but never carries money. Anything that could only ever be read by hand is
  ``manual`` and produces no data at all.
* **``status`` is about wiring, not about permission.** ``enabled`` means the
  code path exists and runs; it never implies the supplier licensed the data for
  anything beyond the local personal use recorded in the spec.

Nothing in this module makes a network call. It is a static description, so it
must be updated by hand whenever a channel is added or a supplier changes terms.
"""

from __future__ import annotations

from dataclasses import dataclass

#: ``quote`` = may carry a sourced price; ``discovery`` = candidate only, never
#: money; ``map`` = coordinates/routes; ``transit`` = taxi estimates;
#: ``llm`` = language only, never facts about hotels; ``manual`` = the user must
#: check it themselves, the project holds no channel.
KINDS = ("quote", "discovery", "map", "transit", "llm", "manual")

#: ``enabled`` = on by default; ``opt_in`` = code exists behind an explicit
#: switch; ``needs_credential`` = code may exist but the local credential or
#: bundle is missing; ``not_available`` = no lawful channel exists at all.
STATUSES = ("enabled", "opt_in", "needs_credential", "not_available")


@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    display_name: str
    kind: str
    channel: str
    status: str
    #: What this channel can actually prove, in plain Chinese.
    provides: tuple[str, ...]
    #: What it must never be used for.
    cannot: tuple[str, ...]
    credential: str
    #: The evidence file that justifies the status.
    evidence: str


REGISTRY: tuple[SourceRecord, ...] = (
    SourceRecord(
        source_id="tuniu",
        display_name="途牛",
        kind="quote",
        channel="官方酒店 MCP（JSON-RPC，只读两工具）",
        status="opt_in",
        provides=(
            "搜索参考价与房型报价",
            "剩余房量 count（官方定义「剩余库存」）",
            "房型最大入住人数",
            "取消规则",
        ),
        cannot=(
            "不返回入住组合回显（成人数/儿童/房数/日期）",
            "不返回报价级来源链接",
            "不返回押金或到店必付明细",
            "无逐夜拆分，rmbPrices 单位未注明",
        ),
        credential=".env TUNIU_API_KEY（+ XUNQI_TUNIU_AUTH_MODE）",
        evidence="docs/project_notes.md",
    ),
    SourceRecord(
        source_id="rollinggo",
        display_name="道旅（RollingGo）",
        kind="quote",
        channel="酒店 MCP（2.0 正式报价源；途牛显式切换，不自动兜底）",
        status="enabled",
        provides=(
            "含税均价（官方口径「每间夜折后总含税均价」）",
            "原样 bookingUrl",
            "取消规则",
            "isOnRequest 立订标记",
        ),
        cannot=(
            "协议没有剩余房量字段，isOnRequest 不是库存",
            "不返回入住组合回显",
            "不返回押金明细",
            "不返回逐夜拆分",
        ),
        credential=".env ROLLINGGO_API_KEY + XUNQI_HOTEL_SOURCE=rollinggo",
        evidence="docs/project_notes.md",
    ),
    SourceRecord(
        source_id="flyai",
        display_name="飞猪（FlyAI）",
        kind="discovery",
        channel="官方 CLI / MCP，只读 search_hotels",
        status="opt_in",
        provides=(
            "酒店候选：名称、地址、星级、近地铁提示",
            "价格展示字符串（如 ¥40，原样保留）",
            "detailUrl 存在性（仅布尔）",
        ),
        cannot=(
            "不返回币种字段，展示字符串不可换算金额",
            "不返回房型、库存、入住组合",
            "经纬度不稳定（同一接口两次实测 19/19 与 0/11）",
            "detailUrl 是跳转壳，不带日期/人数，不可作预订链接",
            "不与途牛/道旅合并为同店或比价",
        ),
        credential=".env FLYAI_API_KEY + XUNQI_FLYAI_MODE=cli",
        evidence="docs/project_notes.md",
    ),
    SourceRecord(
        source_id="amap",
        display_name="高德地图",
        kind="map",
        channel="Web 服务 API",
        status="enabled",
        provides=("POI 召回与精确匹配", "步行/驾车路径与耗时"),
        cannot=("不提供酒店房价", "个人档配额 5,000 次/月基础搜索"),
        credential=".env AMAP_API_KEY",
        evidence="docs/project_notes.md",
    ),
    SourceRecord(
        source_id="tencent_map",
        display_name="腾讯位置服务",
        kind="map",
        channel="Web 服务 API",
        status="opt_in",
        provides=("POI 精确核对", "路线交叉校验"),
        cannot=("不提供酒店房价", "地点搜索个人档仅 200 次/日"),
        credential=".env TENCENT_MAP_KEY / TENCENT_MAP_SECRET",
        evidence="docs/project_notes.md",
    ),
    SourceRecord(
        source_id="didi",
        display_name="滴滴",
        kind="transit",
        channel="官方 MCP，只读估价",
        status="opt_in",
        provides=("打车参考估价",),
        cannot=("不满足交通硬门槛（不给步行/驾车耗时）", "禁止下单/取消/查单"),
        credential=".env DIDI_MCP_KEY",
        evidence="docs/project_notes.md",
    ),
    SourceRecord(
        source_id="deepseek",
        display_name="DeepSeek",
        kind="llm",
        channel="官方 API",
        status="enabled",
        provides=("自然语言解析入住条件",),
        cannot=("不产生任何酒店事实、价格或资格结论",),
        credential=".env DEEPSEEK_API_KEY",
        evidence="docs/project_notes.md",
    ),
    SourceRecord(
        source_id="meituan",
        display_name="美团（旅行助手）",
        kind="manual",
        channel="官方 mttravel CLI，自然语言长文本输出",
        status="needs_credential",
        provides=(
            "可作为人工核价的辅助入口（需本人 Token）",
        ),
        cannot=(
            "价格是脱敏占位符（如 ￥4XX起/晚），不能作为金额证据",
            "输出为自然语言长文，不含结构化房型/库存字段",
            "响应约 1–2 分钟，不能进实时报价链路",
            "不进候选发现源，不参与任何比价",
        ),
        credential="~/.config/meituan-travel/config.json（个人开发者 Token）",
        evidence="docs/project_notes.md",
    ),
    SourceRecord(
        source_id="ctrip",
        display_name="携程",
        kind="manual",
        channel="无个人可用 API（联盟只给转链）",
        status="not_available",
        provides=("仅用户人工核价",),
        cannot=("不提供可缓存房价", "联盟链接不可作数据源"),
        credential="—",
        evidence="docs/project_notes.md",
    ),
    SourceRecord(
        source_id="qunar",
        display_name="去哪儿 / 同程",
        kind="manual",
        channel="无个人可用 API（联盟/企业签约）",
        status="not_available",
        provides=("仅用户人工核价",),
        cannot=("不提供可缓存房价",),
        credential="—",
        evidence="docs/project_notes.md",
    ),
)


def by_id(source_id: str) -> SourceRecord | None:
    """Return one record, or ``None`` for an unknown id (never guess)."""
    for record in REGISTRY:
        if record.source_id == source_id:
            return record
    return None


def quote_sources() -> tuple[SourceRecord, ...]:
    """Channels allowed to carry a price on a card."""
    return tuple(r for r in REGISTRY if r.kind == "quote")


def discovery_sources() -> tuple[SourceRecord, ...]:
    """Channels that may suggest a hotel but never show money."""
    return tuple(r for r in REGISTRY if r.kind == "discovery")


def manual_sources() -> tuple[SourceRecord, ...]:
    """Channels the project deliberately does not hold, listed for the user."""
    return tuple(r for r in REGISTRY if r.kind == "manual")


def summary() -> dict[str, int]:
    """Counts by status, for a health line or a doc table."""
    counts = {status: 0 for status in STATUSES}
    for record in REGISTRY:
        counts[record.status] += 1
    return counts
