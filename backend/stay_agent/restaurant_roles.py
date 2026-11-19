"""Conservative restaurant-role evidence from one map search context."""
from dataclasses import dataclass
import re

AUXILIARY_WORDS = ("外送", "外卖", "配送", "来电", "充电", "停车", "停车场", "充电宝", "自提点", "取餐柜", "售卖机")
DINING_WORDS = ("火锅", "餐厅", "餐饮", "饭店", "酒楼", "小吃", "快餐", "烤肉", "海底捞", "restaurant")
AMAP_DINING_PREFIXES = ("05",)
AMAP_AUXILIARY_PREFIXES = ("01", "07", "15")
TENCENT_DINING_WORDS = ("美食", "餐饮", "火锅", "中餐", "餐厅", "餐馆", "快餐")
TENCENT_AUXILIARY_WORDS = ("充电", "停车", "汽车服务", "交通设施", "生活服务", "外卖", "配送")


@dataclass(frozen=True)
class RoleEvidence:
    role: str
    reason: str
    source_category: str | None


def classify_restaurant(provider: str, name: str, metadata: dict) -> RoleEvidence:
    """Fail closed: a positive name alone never proves dine-in."""
    raw_category = metadata.get("category") if provider == "tencent" else metadata.get("type")
    raw_code = metadata.get("typecode") if provider == "amap" else None
    category = raw_category.strip()[:160] if isinstance(raw_category, str) and raw_category.strip() else None
    code = raw_code.strip()[:64] if isinstance(raw_code, str) and raw_code.strip() else None
    source = " / ".join(v for v in (category, code) if v) or None
    name_aux = any(word in name.lower() for word in AUXILIARY_WORDS)
    name_dining = any(word in name.lower() for word in DINING_WORDS)
    if provider == "amap":
        codes = [c.strip() for c in (code or "").split("|") if c.strip()]
        positive = bool(codes) and all(re.fullmatch(r"05\d{4}", c) for c in codes)
        negative = any(re.fullmatch(r"\d{6}", c) and c.startswith(AMAP_AUXILIARY_PREFIXES) for c in codes)
        # Descriptive category text is supplemental and may be missing.
        positive = positive or (not codes and bool(category) and "餐饮服务" in category)
        negative = negative or bool(category and any(w in category for w in ("充电", "停车", "外卖", "外送")))
    elif provider == "tencent":
        positive = bool(category and any(w in category for w in TENCENT_DINING_WORDS))
        negative = bool(category and any(w in category for w in TENCENT_AUXILIARY_WORDS))
    else:
        return RoleEvidence("unknown", "unsupported_provider", source)
    if name_aux and negative and not positive:
        return RoleEvidence("auxiliary", "auxiliary_name_and_category", source)
    if (positive and negative) or (name_aux and positive) or (negative and name_dining):
        return RoleEvidence("unknown", "conflicting_role_evidence", source)
    if name_aux and not positive:
        return RoleEvidence("auxiliary", "auxiliary_name", source)
    if negative and not positive:
        return RoleEvidence("auxiliary", "auxiliary_category", source)
    if name_dining and positive:
        return RoleEvidence("restaurant_body", "name_and_category", source)
    return RoleEvidence("unknown", "insufficient_role_evidence", source)
