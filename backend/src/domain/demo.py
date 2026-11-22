"""桂桥社区交接簿，含期初结转、跨月借还、退役、重复上传和换标后的新号。"""


# 旧批次：全部按旧编号记录，未使用换标单也照常盘点。
_EVENTS = (
    ("GQ-01", "充电电钻", "2026-08-28", 1, "accept"),
    ("GQ-02", "数字万用表", "2026-08-31", 1, "accept"),
    ("GQ-03", "园艺修枝剪", "2026-09-03", 1, "accept"),
    ("GQ-01", None, "2026-09-12", 1, "check-out"),
    ("GQ-02", None, "2026-09-30", 1, "check-out"),
    ("GQ-02", None, "2026-09-30", 2, "check-in"),
    ("GQ-01", None, "2026-10-01", 1, "check-in"),
    ("GQ-04", "手持砂光机", "2026-10-02", 1, "accept"),
    ("GQ-02", None, "2026-10-31", 1, "retire"),
    ("GQ-03", None, "2026-10-31", 1, "check-out"),
    ("GQ-05", "折叠踏梯", "2026-11-01", 1, "accept"),
    ("GQ-04", None, "2026-11-15", 1, "check-out"),
    ("GQ-01", None, "2026-11-20", 1, "check-out"),
    ("GQ-03", None, "2026-11-20", 1, "check-in"),
    ("GQ-01", None, "2026-11-21", 1, "check-in"),
    ("GQ-06", "棘轮扳手套装", "2026-11-21", 1, "accept"),
)

# 换标后批次：11 月 20 日的归还按新号 GQ-03B 记录，旧号在 11 月 21 日重新接收发给别的工具。
_RELABELED_EVENTS = tuple(
    event for event in _EVENTS if event[:2] != ("GQ-03", None) or event[2] != "2026-11-20"
) + (
    ("GQ-03B", None, "2026-11-20", 1, "check-in"),
    ("GQ-03", "园艺修枝剪", "2026-11-21", 1, "accept"),
)

# 演示换标单：11 月 01 日起 GQ-03 → GQ-03B；换标交接单在 11 月 20 日才随新号归还补录。
_RELABELS = (
    ("GQ-R01", "GQ-03", "GQ-03B", "2026-11-01", None),
)


def _rows(events) -> list[dict]:
    rows = [
        {"event_id": f"GQ-E{index:03d}", "asset_id": asset_id, "asset_name": asset_name,
         "event_date": day, "sequence": sequence, "event_type": event_type}
        for index, (asset_id, asset_name, day, sequence, event_type) in enumerate(events, 1)
    ]
    rows.append(rows[9].copy())
    return rows


def demo_rows() -> list[dict]:
    """未交换标单的旧批次，照常盘点。"""
    return _rows(_EVENTS)


def relabeled_rows() -> list[dict]:
    """换标后批次：出现新号，需要配合换标单才能接回原实物。"""
    return _rows(_RELABELED_EVENTS)


def demo_relabel_rows() -> list[dict]:
    return [
        {"relabel_id": relabel_id, "old_asset_id": old_asset_id, "new_asset_id": new_asset_id,
         "effective_from": effective_from, "effective_to": effective_to}
        for relabel_id, old_asset_id, new_asset_id, effective_from, effective_to in _RELABELS
    ]
