"""换标单校验、生效期冲突判定，以及按交接日把旧号接回同一件实物。"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from .spec import SPEC

_FIELDS = {item["key"]: item for item in SPEC["relabel"]["fields"]}


@dataclass(frozen=True)
class Relabel:
    relabel_id: str
    old_asset_id: str
    new_asset_id: str
    effective_from: date
    effective_to: date | None


@dataclass
class Resolution:
    """回放前的实物认领结果：事件落到哪个实物上，哪些换标单与交接挂起。"""

    item_keys: dict[int, str] = field(default_factory=dict)
    links: dict[int, str] = field(default_factory=dict)
    suspended: set[int] = field(default_factory=set)
    suspended_tools: set[str] = field(default_factory=set)
    suspended_relabels: set[int] = field(default_factory=set)
    aliases: dict[str, list[tuple[date, str]]] = field(default_factory=dict)
    conflict_reason: dict[str, str] = field(default_factory=dict)
    hints: dict[int, str] = field(default_factory=dict)


def _text(row, key, index):
    meta = _FIELDS[key]
    raw = row.get(key)
    if isinstance(raw, str):
        value = raw.strip()
        if value and len(value) <= meta["max_length"]:
            return value
    raise ValueError(f"换标单第 {index} 行的{meta['label']}须为 1 至 {meta['max_length']} 个字符。")


def _date(row, key, index):
    raw = row.get(key)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        if _FIELDS[key].get("required", True):
            raise ValueError(f"换标单第 {index} 行的{_FIELDS[key]['label']}不能为空。")
        return None
    try:
        return date.fromisoformat(str(raw).strip())
    except (TypeError, ValueError):
        raise ValueError(f"换标单第 {index} 行的{_FIELDS[key]['label']}须为有效日期。") from None


def ordered(rows) -> list[Relabel]:
    """校验换标单并去重；同一单号内容冲突时要求先补正。"""
    if not isinstance(rows, (list, tuple)):
        raise ValueError("换标单格式有误，请导入 CSV。")
    by_id = {}
    seen_mapping = set()
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f"换标单第 {index} 行格式有误。")
        relabel_id = _text(row, "relabel_id", index)
        old_asset_id = _text(row, "old_asset_id", index)
        new_asset_id = _text(row, "new_asset_id", index)
        if old_asset_id == new_asset_id:
            raise ValueError(f"换标单 {relabel_id} 的原编号与新编号不能相同，请补正换标单。")
        effective_from = _date(row, "effective_from", index)
        effective_to = _date(row, "effective_to", index)
        if effective_to is not None and effective_to < effective_from:
            raise ValueError(f"换标单 {relabel_id} 的失效交接日不能早于生效交接日。")
        item = Relabel(relabel_id, old_asset_id, new_asset_id, effective_from, effective_to)
        previous = by_id.get(relabel_id)
        if previous == item:
            continue
        if previous is not None:
            raise ValueError(f"换标单号 {relabel_id} 的内容冲突，请核对原始换标单。")
        mapping = (old_asset_id, new_asset_id, effective_from, effective_to)
        if mapping in seen_mapping:
            # 同一映射重复登记，保留首个单号。
            continue
        seen_mapping.add(mapping)
        by_id[relabel_id] = item
    return sorted(by_id.values(), key=lambda item: (item.effective_from, item.relabel_id))


def normalize_rows(rows) -> list[dict]:
    return [
        {"relabel_id": item.relabel_id, "old_asset_id": item.old_asset_id,
         "new_asset_id": item.new_asset_id, "effective_from": item.effective_from.isoformat(),
         "effective_to": item.effective_to.isoformat() if item.effective_to else None}
        for item in ordered(rows)
    ]


def _overlaps(left: Relabel, right: Relabel) -> bool:
    left_end = left.effective_to or date.max
    right_end = right.effective_to or date.max
    return left.effective_from <= right_end and right.effective_from <= left_end


def conflicts(relabels: list[Relabel]):
    """判定生效期冲突破换标单，并把冲突沿工具编号传递到相关换标单。

    两处认领在重叠生效期内指向不同结果时才算冲突：同一旧编号改投不同新编号，
    或同一新编号被不同旧编号认领。链式换标（A→B 后再 B→C）是正常接续，不在此列。
    与冲突换标单共用任一工具编号的其他换标单一并挂起，避免已挂起的工具再被认走。
    """
    tools = [{item.old_asset_id, item.new_asset_id} for item in relabels]
    suspended = set()
    for left in range(len(relabels)):
        for right in range(left + 1, len(relabels)):
            if not _overlaps(relabels[left], relabels[right]):
                continue
            same_old = relabels[left].old_asset_id == relabels[right].old_asset_id
            same_new = relabels[left].new_asset_id == relabels[right].new_asset_id
            if (same_old and relabels[left].new_asset_id != relabels[right].new_asset_id) or (
                    same_new and relabels[left].old_asset_id != relabels[right].old_asset_id):
                suspended.update((left, right))
    changed = True
    while changed:
        changed = False
        conflicting_tools = {tool for index in suspended for tool in tools[index]}
        for index in range(len(relabels)):
            if index not in suspended and tools[index] & conflicting_tools:
                suspended.add(index)
                changed = True

    suspended_tools = {tool for index in suspended for tool in tools[index]}
    reasons = {}
    for tool_id in suspended_tools:
        names = "、".join(sorted(relabels[index].relabel_id for index in suspended if tool_id in tools[index]))
        reasons[tool_id] = f"换标单 {names} 的生效期重叠"
    return suspended, suspended_tools, reasons


def resolve(events, relabels) -> Resolution:
    """按交接日合并换标单和交接事件，为每条事件确定实物。

    换标单生效后原实物在旧号和新号下都能被认回，跨次换标的借还因此接续；
    旧号再次接收时说明旧号已发给别的工具，另起一件实物。
    """
    relabels = list(relabels)
    suspended_relabels, suspended_tools, conflict_reason = conflicts(relabels)
    result = Resolution(suspended_tools=suspended_tools, suspended_relabels=suspended_relabels,
                        conflict_reason=conflict_reason)
    bound: dict[str, str] = {}
    released: dict[str, str] = {}
    lifecycle: defaultdict = defaultdict(int)
    linked: dict[int, str] = {}

    def remember(item_key, day, asset_id):
        names = result.aliases.setdefault(item_key, [])
        if not names or names[-1][1] != asset_id:
            names.append((day, asset_id))

    by_day = defaultdict(lambda: {"relabels": [], "events": []})
    for index, item in enumerate(relabels):
        by_day[item.effective_from]["relabels"].append(index)
    for index, event in enumerate(events):
        by_day[event.event_date]["events"].append(index)

    for day in sorted(by_day):
        group = by_day[day]
        remaining = [index for index in group["relabels"] if index not in suspended_relabels]
        # 同一天的链式换标先接已认领的旧号，避免 A→B、B→C 因登记顺序不同而断链。
        while remaining:
            progressed = False
            for index in list(remaining):
                relabel = relabels[index]
                item_key = bound.get(relabel.old_asset_id)
                if item_key is None:
                    continue
                existing = bound.get(relabel.new_asset_id)
                if existing is not None and existing != item_key:
                    raise ValueError(
                        f"换标单 {relabel.relabel_id} 的新编号 {relabel.new_asset_id} 在生效交接日已有流转，"
                        "请补正换标单后重新盘点。"
                    )
                bound[relabel.new_asset_id] = item_key
                released[relabel.old_asset_id] = item_key
                linked[index] = item_key
                result.links[index] = item_key
                remember(item_key, day, relabel.new_asset_id)
                remaining.remove(index)
                progressed = True
            if not progressed:
                for index in remaining:
                    # 旧号此前没有在管生命周期，换标单只作备案，不改变实物归属。
                    result.hints[index] = "旧号在生效交接日没有在管流转，换标单只作备案"
                break

        for index in sorted(group["events"], key=lambda item: (events[item].asset_id, events[item].sequence)):
            event = events[index]
            if event.asset_id in suspended_tools:
                result.suspended.add(index)
                continue
            item_key = bound.get(event.asset_id)
            if event.event_type == "accept" and (item_key is None or released.get(event.asset_id) == item_key):
                # 换标后旧号重新接收：旧号已发给别的工具，另起一件实物。
                lifecycle[event.asset_id] += 1
                item_key = f"{event.asset_id}#{lifecycle[event.asset_id]}"
                bound[event.asset_id] = item_key
                released.pop(event.asset_id, None)
                remember(item_key, day, event.asset_id)
            result.item_keys[index] = item_key

    return result
