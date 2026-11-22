"""按日终回放交接事件，计算快照和区间勾稽。

换标单在回放前按生效交接日把原编号接回同一件实物；换标单生效期冲突时，
涉及工具的整段流转挂起，其他工具照常回放。
"""

from calendar import monthrange
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, timedelta

from . import relabel as relabel_domain
from .spec import SPEC


_TRANSITIONS = {
    (None, "accept"): "in_custody",
    ("in_custody", "check-out"): "checked_out",
    ("checked_out", "check-in"): "in_custody",
    ("in_custody", "retire"): "retired",
}
_STATUS_LABELS = {None: "未接收", "in_custody": "在管", "checked_out": "借出", "retired": "退役"}
_EVENT_LABELS = {"accept": "接收", "check-out": "借出", "check-in": "归还", "retire": "退役"}
_FIELD_BY_KEY = {field["key"]: field for field in SPEC["fields"]}
_PARAM_DEFAULTS = {field["key"]: field["default"] for field in SPEC["params"]}
_BALANCE_COLUMNS = (
    ("start_date", "统计首日"), ("as_of_date", "截止日"), ("close_type", "快照类型"),
    ("opening_active", "期初有效"), ("opening_in_custody", "期初在管"),
    ("opening_checked_out", "期初借出"), ("accepted", "接收"), ("checked_out", "借出"),
    ("checked_in", "归还"), ("retired", "退役"), ("closing_active", "期末有效"),
    ("closing_in_custody", "期末在管"), ("closing_checked_out", "期末借出"),
    ("in_custody_rate_pct", "期末在管率（%）"), ("active_difference", "有效勾稽差额"),
    ("custody_difference", "在管勾稽差额"), ("checked_out_difference", "借出勾稽差额"),
)
_ASSET_COLUMNS = (
    ("asset_id", "工具编号"), ("asset_name", "工具名称"), ("status", "期末状态"),
    ("last_event_date", "末笔交接日"), ("last_sequence", "末笔顺序号"),
    ("last_event_type", "末笔交接类型"), ("last_event_id", "末笔事件编号"),
)
_RELABEL_COLUMNS = (
    ("relabel_id", "换标单号"), ("old_asset_id", "原编号"), ("new_asset_id", "新编号"),
    ("effective_from", "生效交接日"), ("effective_to", "失效交接日"), ("state", "认领状态"),
    ("claimed_events", "认回交接（条）"), ("claimed_check_ins", "其中归还（条）"), ("note", "说明"),
)
_SUSPENDED_COLUMNS = (
    ("event_date", "交接日期"), ("sequence", "顺序号"), ("event_id", "事件编号"),
    ("asset_id", "工具编号"), ("asset_name", "工具名称"), ("event_type", "交接类型"), ("reason", "挂起原因"),
)


@dataclass(frozen=True)
class _Event:
    event_id: str
    asset_id: str
    asset_name: str | None
    event_date: date
    sequence: int
    event_type: str


@dataclass(frozen=True)
class _Entry:
    key: str
    event: _Event


@dataclass(frozen=True)
class _Asset:
    name: str
    status: str
    last_event: _Event


def _day(value, label):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label}须为有效日期。") from None


def _event_text(row, key, index, *, required=True):
    field = _FIELD_BY_KEY[key]
    raw = row.get(key)
    if raw is None and not required:
        return None
    if isinstance(raw, str):
        value = raw.strip()
        if not value and not required:
            return None
        if value and len(value) <= field["max_length"]:
            return value
    raise ValueError(f"第 {index} 行的{field['label']}须为 1 至 {field['max_length']} 个字符。")


def _ordered_events(rows):
    if not isinstance(rows, (list, tuple)) or not rows:
        raise ValueError("交接明细为空，请导入至少一条生命周期事件。")
    by_id = {}
    by_slot = {}
    sequence_field = _FIELD_BY_KEY["sequence"]
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f"第 {index} 行交接事件格式有误。")
        kind = row.get("event_type")
        if kind not in _FIELD_BY_KEY["event_type"]["options"]:
            raise ValueError(f"第 {index} 行事件类型无效，须为 accept、check-out、check-in 或 retire。")
        sequence = row.get("sequence")
        if type(sequence) is not int or not sequence_field["min"] <= sequence <= sequence_field["max"]:
            raise ValueError(f"第 {index} 行顺序号须为 1 至 {sequence_field['max']} 的整数。")
        event = _Event(
            _event_text(row, "event_id", index), _event_text(row, "asset_id", index),
            _event_text(row, "asset_name", index, required=kind == "accept"),
            _day(row.get("event_date"), f"第 {index} 行交接日期"), sequence, kind,
        )
        previous = by_id.get(event.event_id)
        if previous == event:
            continue
        if previous is not None:
            raise ValueError(f"事件编号 {event.event_id} 的内容冲突，请核对原始交接单。")
        slot = (event.asset_id, event.event_date, event.sequence)
        if slot in by_slot:
            raise ValueError(f"工具 {event.asset_id} 在 {event.event_date} 的顺序号 {sequence} 重复"
                             f"（{by_slot[slot]}、{event.event_id}）。")
        by_slot[slot] = event.event_id
        by_id[event.event_id] = event
    return sorted(by_id.values(), key=lambda event: (event.event_date, event.asset_id, event.sequence))


def validate_rows(rows):
    """校验事件格式与重放键，返回去重后的规范事件；状态由盘点截止日决定。"""
    return [dict(asdict(event), event_date=event.event_date.isoformat()) for event in _ordered_events(rows)]


def validate_relabel_rows(rows):
    """校验换标单格式，返回按生效交接日排序、去重后的规范换标单。"""
    return relabel_domain.normalize_rows(rows)


class _Ledger:
    def __init__(self, entries):
        self.assets = {}
        self.counts = Counter()
        self._entries = iter(entries)
        self._next = next(self._entries, None)

    def advance(self, boundary, *, inclusive=True):
        movements = Counter()
        while (entry := self._next) is not None:
            event = entry.event
            if event.event_date > boundary or (event.event_date == boundary and not inclusive):
                break
            asset = self.assets.get(entry.key)
            prior = asset.status if asset else None
            status = _TRANSITIONS.get((prior, event.event_type))
            if status is None:
                raise ValueError(f"事件 {event.event_id}（工具 {event.asset_id}，{event.event_date}）"
                                 f"无法执行{_EVENT_LABELS[event.event_type]}，当前状态为{_STATUS_LABELS[prior]}。")
            if asset:
                self.counts[prior] -= 1
            name = asset.name if asset else event.asset_name
            self.assets[entry.key] = _Asset(name, status, event)
            self.counts[status] += 1
            movements[event.event_type] += 1
            self._next = next(self._entries, None)
        return movements

    def balance(self):
        in_custody = self.counts["in_custody"]
        checked_out = self.counts["checked_out"]
        return in_custody + checked_out, in_custody, checked_out


def _balance_row(start, close, close_type, opening, closing, movements):
    opening_active, opening_custody, opening_out = opening
    active, custody, out = closing
    accepted = movements["accept"]
    checked_out = movements["check-out"]
    checked_in = movements["check-in"]
    retired = movements["retire"]
    return {
        "start_date": start.isoformat(), "as_of_date": close.isoformat(), "close_type": close_type,
        "opening_active": opening_active, "opening_in_custody": opening_custody, "opening_checked_out": opening_out,
        "accepted": accepted, "checked_out": checked_out, "checked_in": checked_in, "retired": retired,
        "closing_active": active, "closing_in_custody": custody, "closing_checked_out": out,
        "in_custody_rate_pct": round(100 * custody / active, 4) if active else None,
        "active_difference": active - (opening_active + accepted - retired),
        "custody_difference": custody - (opening_custody + accepted + checked_in - checked_out - retired),
        "checked_out_difference": out - (opening_out + checked_out - checked_in),
    }


def _monthly_balances(ledger, start, cutoff):
    monthly = []
    totals = Counter()
    while True:
        month_end = start.replace(day=monthrange(start.year, start.month)[1])
        close = min(month_end, cutoff)
        opening = ledger.balance()
        movements = ledger.advance(close)
        totals.update(movements)
        close_type = "自然月末" if close == month_end else "截止日"
        monthly.append(_balance_row(start, close, close_type, opening, ledger.balance(), movements))
        if close == cutoff:
            return monthly, totals
        start = close + timedelta(days=1)


def _table(title, columns, rows):
    return {"title": title, "columns": [{"key": key, "label": label} for key, label in columns], "rows": rows}


def _label_at(resolution, key, cutoff):
    names = [asset_id for day, asset_id in resolution.aliases.get(key, ()) if day <= cutoff]
    if names:
        return names[-1]
    return key.split("#")[0]


def _claim_rows(relabels, resolution, events, cutoff):
    rows = []
    for index, item in enumerate(relabels):
        claimed = claimed_check_ins = 0
        linked_key = resolution.links.get(index)
        if linked_key is not None:
            for event_index, event in enumerate(events):
                if (event_index in resolution.suspended or event.event_date > cutoff
                        or event.event_date < item.effective_from
                        or resolution.item_keys.get(event_index) != linked_key):
                    continue
                if event.asset_id in (item.old_asset_id, item.new_asset_id):
                    claimed += 1
                    if event.event_type == "check-in":
                        claimed_check_ins += 1
        if index in resolution.suspended_relabels:
            state = "挂起"
        elif linked_key is not None:
            state = "已认领"
        elif item.effective_from > cutoff:
            state = "未生效"
        else:
            state = "未适用"
        rows.append({
            "relabel_id": item.relabel_id, "old_asset_id": item.old_asset_id,
            "new_asset_id": item.new_asset_id, "effective_from": item.effective_from.isoformat(),
            "effective_to": item.effective_to.isoformat() if item.effective_to else "",
            "state": state, "claimed_events": claimed, "claimed_check_ins": claimed_check_ins,
            "note": (resolution.conflict_reason.get(item.old_asset_id, "") if state == "挂起"
                     else resolution.hints.get(index, "")),
        })
    return rows


def _suspended_rows(events, resolution, cutoff):
    return [
        {
            "event_date": events[index].event_date.isoformat(), "sequence": events[index].sequence,
            "event_id": events[index].event_id, "asset_id": events[index].asset_id,
            "asset_name": events[index].asset_name or "",
            "event_type": _EVENT_LABELS[events[index].event_type],
            "reason": resolution.conflict_reason.get(events[index].asset_id, "换标单生效期重叠"),
        }
        for index in sorted(resolution.suspended) if events[index].event_date <= cutoff
    ]


def analyze(rows, normalized_params, relabel_rows=None, relabel_source=None):
    """生成日终资产明细、逐月勾稽及整段期初期末。"""
    if not isinstance(normalized_params, dict) or normalized_params.keys() - _PARAM_DEFAULTS.keys():
        raise ValueError("盘点参数格式有误，请核对统计首日和盘点截止日。")
    params = {**_PARAM_DEFAULTS, **normalized_params}
    start = _day(params["start_date"], "统计首日")
    cutoff = _day(params["as_of_date"], "盘点截止日")
    if start > cutoff:
        raise ValueError("统计首日须早于或等于盘点截止日。")

    events = _ordered_events(rows)
    relabels = relabel_domain.ordered(relabel_rows or [])
    resolution = relabel_domain.resolve(events, relabels) if relabels else relabel_domain.Resolution()
    entries = [
        _Entry(resolution.item_keys.get(index) or event.asset_id, event)
        for index, event in enumerate(events) if index not in resolution.suspended
    ]

    ledger = _Ledger(entries)
    ledger.advance(start, inclusive=False)
    opening = ledger.balance()
    monthly, totals = _monthly_balances(ledger, start, cutoff)
    closing = ledger.balance()
    period = _balance_row(start, cutoff, "区间期末", opening, closing, totals)
    active, custody, out = closing
    reconciled = all(row[key] == 0 for row in [period, *monthly]
                     for key in ("active_difference", "custody_difference", "checked_out_difference"))

    asset_columns = list(_ASSET_COLUMNS)
    if relabels:
        index = [item[0] for item in asset_columns].index("asset_name") + 1
        asset_columns.insert(index, ("alias_ids", "编号沿革"))
    assets = [
        {
            "asset_id": _label_at(resolution, key, cutoff), "asset_name": asset.name,
            "alias_ids": (" → ".join(asset_id for _, asset_id in resolution.aliases.get(key, ()))
                          or _label_at(resolution, key, cutoff)),
            "status": _STATUS_LABELS[asset.status],
            "last_event_date": asset.last_event.event_date.isoformat(), "last_sequence": asset.last_event.sequence,
            "last_event_type": _EVENT_LABELS[asset.last_event.event_type],
            "last_event_id": asset.last_event.event_id,
        }
        for key, asset in sorted(ledger.assets.items(), key=lambda pair: _label_at(resolution, pair[0], cutoff))
    ]
    claims = _claim_rows(relabels, resolution, events, cutoff)
    suspended = _suspended_rows(events, resolution, cutoff)
    conflicted = sorted(resolution.suspended_tools)

    tables = [
        _table("区间期初期末勾稽", _BALANCE_COLUMNS, [period]),
        _table("月末与截止日勾稽", _BALANCE_COLUMNS, monthly),
        _table("期末资产快照", asset_columns, assets),
    ]
    metrics = [
        {"label": "期末有效资产", "value": active, "detail": "在管与借出工具之和"},
        {"label": "期末在管", "value": custody, "detail": "已接收或已归还且仍在站内的工具"},
        {"label": "期末借出", "value": out, "detail": "截至期末仍未归还"},
        {"label": "期末在管率（%）", "value": period["in_custody_rate_pct"],
         "detail": f"{custody} / {active}" if active else "有效资产为 0，在管率留空"},
    ]
    sections = [{"title": "勾稽与盘点复核", "text": (
        ("各期有效、在管及借出数量的勾稽差额均为 0。" if reconciled else "存在勾稽差额，请核对交接事件。")
        + "逐月期末存量与比率按截止日取值。复核时可从期末资产快照定位末笔交接单。"
    )}]

    headline = f"截至 {cutoff}，有效工具 {active} 件，在管 {custody} 件，借出 {out} 件。"
    note = f"统计期间为 {start} 至 {cutoff}，含首尾两日。期末取截止日日终，流量按事件发生日归属。"
    if relabels:
        tables.append(_table("换标认领依据", _RELABEL_COLUMNS, claims))
        tables.append(_table("挂起的交接单", _SUSPENDED_COLUMNS, suspended))
        metrics.append({"label": "换标认领", "value": sum(1 for row in claims if row["state"] == "已认领"),
                        "detail": f"换标单 {len(relabels)} 张，按生效交接日认回实物"})
        if conflicted:
            metrics.append({"label": "挂起交接", "value": len(suspended),
                            "detail": f"涉及工具 {len(conflicted)} 件，整段流转未计入"})
            headline += (f"换标单生效期冲突，工具 {'、'.join(conflicted)} 的整段流转挂起，"
                         "其余工具照常盘点。")
        source = relabel_source or {}
        version = " · ".join(str(part) for part in (
            source.get("name"), (source.get("checksum") or "")[:12], f"{len(relabels)} 张换标单") if part)
        note += f"本次按发起时固定的换标单版本计算（{version}）。"
        sections.append({"title": "换标认领与挂起", "text": (
            "换标单按生效交接日把原编号接回同一件实物，跨次换标的借出与归还在实物链上接续；"
            "换标后旧号重新接收时另起一件实物。"
            + ("生效期重叠的换标单涉及工具的整段流转挂起，未计入有效资产与在管率；补正换标单后可发起新的盘点。"
               if conflicted else "本期换标单未发现生效期重叠。")
            + "认领依据与挂起交接单见上表，导出 CSV 时随报告一并保留。"
        )})
    elif relabel_source is not None:
        note += "本次运行未选择换标单，按原始编号回放。"

    return {
        "state": "ok" if active and reconciled and not conflicted else "warning",
        "headline": headline,
        "note": note,
        "metrics": metrics,
        "tables": tables,
        "charts": [
            {"title": title, "kind": "line",
             "series": [{"label": row["as_of_date"], "value": row[key]} for row in monthly]}
            for title, key in (("各盘点日有效资产（件）", "closing_active"), ("各盘点日在管工具（件）", "closing_in_custody"))
        ],
        "sections": sections,
        "relabel": {
            "sheet_id": (relabel_source or {}).get("sheet_id"),
            "name": (relabel_source or {}).get("name"),
            "checksum": (relabel_source or {}).get("checksum"),
            "version_count": len(relabels),
            "claimed": sum(1 for row in claims if row["state"] == "已认领"),
            "suspended_events": len(suspended),
            "suspended_tools": conflicted,
        },
    }
