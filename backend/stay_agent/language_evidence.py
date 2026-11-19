"""Narrow deterministic wording helpers; no geographic coverage guarantee."""
import re

# Recognizable prefixes only: unknown names remain unverified, never geocoded here.
_OUTSIDE_PREFIXES = (
    "东京", "東京", "大阪", "京都", "日本", "首尔", "韩国", "新加坡", "曼谷", "泰国",
    "伦敦", "巴黎", "纽约", "美国", "英国", "法国", "悉尼", "澳大利亚",
    "香港", "澳门", "澳門", "台湾", "臺灣", "台北", "臺北", "高雄",
)

def outside_mainland_hint(place):
    if not isinstance(place, str):
        return False
    place = place.strip()
    if place.startswith(_OUTSIDE_PREFIXES):
        return True
    return bool(re.match(
        r"(?:tokyo|osaka|kyoto|seoul|singapore|bangkok|london|paris|new york|"
        r"sydney|hong kong|macau|macao|taipei|taiwan)(?:\b|[\u4e00-\u9fff])", place, re.I
    ))


def chinese_integer(text):
    """Parse explicit Chinese integer amounts, not colloquial implied units."""
    digits = {c: i for i, c in enumerate("零一二三四五六七八九")}
    digits.update({"两": 2, "〇": 0})
    units = {"十": 10, "百": 100, "千": 1000, "万": 10000}
    total = section = number = 0
    for char in text:
        if char in digits:
            number = digits[char]
        elif char == "万":
            total += (section + number) * 10000
            section = number = 0
        elif char in units:
            section += (number or 1) * units[char]
            number = 0
        else:
            return None
    return total + section + number
