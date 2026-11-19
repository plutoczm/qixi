"""Versioned, fully synthetic Guangzhou-only 1.0 Agent test fixture.

The fixture exists because enterprise MCP permissions are unavailable. It
tests deterministic rules, state transitions and UI, never supplier evidence.
Hotel and restaurant brands are invented; district, road and metro names are
plausible Guangzhou geography. Every price, inventory, score, route, fare and
opening value is synthetic and must never be presented as a live quote.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
from typing import Any

DATASET_ID = "xunqi-guangzhou-agent-fixture-v1"
DATASET_VERSION = "1.1.0"
DATASET_LABEL = "广州离线测试夹具"
CITY = "广州"
GENERATED_AT = "2026-11-09T00:00:00+00:00"
DISCOVERY_CITY_MESSAGE = "1.0 离线测试夹具只覆盖广州；不支持其他城市，未查询外部来源。"

HOTELS: tuple[dict[str, Any], ...] = (
    {
        "id": 910101,
        "name": "样例·芳村栖居（花地湾店）",
        "address": "广州市荔湾区花地大道中288号",
        "district": "荔湾区",
        "reference_price_fen": 16900,
        "hotel_stars": 4.0,
        "guest_score": 4.6,
        "review_count": 328,
        "restaurant": "样例·潮汕牛肉火锅（荔胜广场店）",
        "restaurant_address": "广州市荔湾区花地大道中66号荔胜广场6楼",
        "restaurant_district": "荔湾区",
        "walk_meters": 1200,
        "walk_seconds": 900,
        "drive_meters": 2600,
        "drive_seconds": 660,
        "subway_name": "芳村站H口",
        "subway_walk_meters": 480,
        "subway_walk_seconds": 360,
        "opening_today": "10:30-次日02:00（合成样例）",
        "opening_week": "周一至周日 10:30-次日02:00（合成样例）",
        "fare_lower_fen": 1200,
        "fare_upper_fen": 1400,
        "quotations": (
            ("高级大床房", "不含早 · 预付价", 16900, 4,
             "入住前一日18:00前可免费取消，之后不可退", True),
            ("标准双床房", "含双早 · 灵活价", 18800, 3,
             "入住当日12:00前可免费取消", True),
            ("公共卫浴床位", "多人间单床位", 6000, 8,
             "不可取消、不可退款", False),
        ),
    },
    {
        "id": 910102,
        "name": "样例·沙面巷宿（黄沙地铁站店）",
        "address": "广州市荔湾区丛桂路123号",
        "district": "荔湾区",
        "reference_price_fen": 14900,
        "hotel_stars": 3.5,
        "guest_score": 4.4,
        "review_count": 512,
        "restaurant": "样例·潮汕牛肉火锅（荔胜广场店）",
        "restaurant_address": "广州市荔湾区花地大道中66号荔胜广场6楼",
        "restaurant_district": "荔湾区",
        "walk_meters": 2188,
        "walk_seconds": 1750,
        "drive_meters": 4025,
        "drive_seconds": 816,
        "subway_name": "黄沙站C口",
        "subway_walk_meters": 584,
        "subway_walk_seconds": 480,
        "opening_today": "10:30-次日02:00（合成样例）",
        "opening_week": "周一至周日 10:30-次日02:00（合成样例）",
        "fare_lower_fen": 1800,
        "fare_upper_fen": 2200,
        "quotations": (
            ("特惠大床房", "限时预付 · 不含早", 14900, 2,
             "不可取消、不可退款", False),
            ("舒适双床房", "标准价 · 含早", 17900, 5,
             "入住前一日18:00前可免费取消", True),
            ("江景大床房", "高楼层 · 含早", 20500, 6,
             "入住前一日18:00前可免费取消", True),
        ),
    },
    {
        "id": 910103,
        "name": "样例·越秀栖处（公园前店）",
        "address": "广州市越秀区中山五路193号",
        "district": "越秀区",
        "reference_price_fen": 13200,
        "hotel_stars": 3.0,
        "guest_score": None,
        "review_count": None,
        "restaurant": "样例·广式点心楼（北京路店）",
        "restaurant_address": "广州市越秀区惠福东路455号3楼",
        "restaurant_district": "越秀区",
        "walk_meters": 232,
        "walk_seconds": 186,
        "drive_meters": 900,
        "drive_seconds": 420,
        "subway_name": "公园前站D口",
        "subway_walk_meters": 180,
        "subway_walk_seconds": 150,
        "opening_today": "08:00-22:00（合成样例）",
        "opening_week": "周一至周日 08:00-22:00（合成样例）",
        "fare_lower_fen": 900,
        "fare_upper_fen": 1100,
        "quotations": (
            ("静谧大床房", "基础价 · 不含早", 13200, 3,
             "入住前一日18:00前可免费取消", True),
            ("商务双床房", "含双早", 15800, 4,
             "不可取消、不可退款", False),
        ),
    },
)

DISCOVERY_CANDIDATES: tuple[dict[str, Any], ...] = (
    {
        "name": "样例·白鹅潭公寓（芳村店）",
        "address": "广州市荔湾区芳村大道中200号",
        "price_text": "¥144",
        "star": "舒适型",
        "nearby_transit": "近芳村地铁站",
    },
    {
        "name": "样例·西关小院（长寿路店）",
        "address": "广州市荔湾区宝华路76号",
        "price_text": "¥168",
        "star": "经济型",
        "nearby_transit": "近长寿路地铁站",
    },
)

DATASET_NOTICE = (
    f"离线 Agent 测试夹具 · {DATASET_LABEL} {DATASET_ID} v{DATASET_VERSION}；"
    "仅覆盖广州，酒店与餐厅为虚构样例，真实地理语境配合成值；"
    "不是来源接口、实时查源、真实身份或可订报价。"
)


def dataset_path() -> Path:
    return Path(__file__).with_name("offline_dataset.json")


def dataset_bundle() -> dict[str, Any]:
    """The exact frozen payload that the versioned JSON file must contain."""
    return {
        "dataset_id": DATASET_ID,
        "version": DATASET_VERSION,
        "label": DATASET_LABEL,
        "city": CITY,
        "generated_at": GENERATED_AT,
        "purpose": "deterministic Agent rule, state and UI regression fixture",
        "synthetic": True,
        "license": "project-owned synthetic test fixture; no third-party dataset used",
        "notice": DATASET_NOTICE,
        "sources": [],
        "_scope": {
            "hotels": "invented brands; Guangzhou district/road/metro context",
            "restaurants": "invented brands; Guangzhou district/road context",
            "values": "all prices, inventory, scores, routes, fares and opening hours are synthetic",
            "city": "广州 only",
        },
        "hotels": [
            {
                **{key: value for key, value in hotel.items() if key != "quotations"},
                "quotations": [
                    {
                        "room_name": room,
                        "plan_name": plan,
                        "amount_fen": amount,
                        "remaining": remaining,
                        "cancellation": cancellation,
                        "cancelable": cancelable,
                    }
                    for room, plan, amount, remaining, cancellation, cancelable
                    in hotel["quotations"]
                ],
            }
            for hotel in HOTELS
        ],
        "discovery_candidates": list(DISCOVERY_CANDIDATES),
    }


def write_dataset_bundle(path: Path | None = None) -> dict[str, Any]:
    """Write a versioned, self-describing JSON bundle for the 1.0 dataset."""
    target = path or dataset_path()
    payload = dataset_bundle()
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def load_dataset_bundle(path: Path | None = None) -> dict[str, Any]:
    """Load the frozen JSON dataset, failing closed if it is absent or altered."""
    target = path or dataset_path()
    payload = json.loads(target.read_text(encoding="utf-8"))
    if payload != dataset_bundle():
        raise ValueError("offline dataset bundle mismatch")
    return payload


def observed_at() -> str:
    return datetime.now(timezone.utc).isoformat()
