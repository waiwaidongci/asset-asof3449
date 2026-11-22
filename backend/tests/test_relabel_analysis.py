"""换标认领、冲突挂起和期末在管率的领域回归测试（不依赖数据库）。"""

import unittest

from src.domain.analysis import analyze
from src.domain.demo import demo_relabel_rows, demo_rows, relabeled_rows
from src.domain.relabel import conflicts, ordered


PARAMS = {"start_date": "2026-09-01", "as_of_date": "2026-11-21"}


def rows(*events):
    return [
        {"event_id": f"E{index:03d}", "asset_id": asset_id, "asset_name": name,
         "event_date": day, "sequence": sequence, "event_type": kind}
        for index, (asset_id, name, day, sequence, kind) in enumerate(events, 1)
    ]


def relabels(*items):
    return [
        {"relabel_id": relabel_id, "old_asset_id": old, "new_asset_id": new,
         "effective_from": start, "effective_to": end}
        for relabel_id, old, new, start, end in items
    ]


def table(result, title):
    return next(item for item in result["tables"] if item["title"] == title)


class LegacyBatchTest(unittest.TestCase):
    def test_batch_without_relabel_is_unchanged(self):
        result = analyze(demo_rows(), PARAMS)
        self.assertEqual(result["headline"], "截至 2026-11-21，有效工具 5 件，在管 4 件，借出 1 件。")
        self.assertFalse([item for item in result["tables"] if item["title"] == "换标认领依据"])

    def test_legacy_batch_ignores_unused_relabel_sheet(self):
        # 未交换标单的旧批次照常盘点，不受换标单版本影响。
        result = analyze(demo_rows(), PARAMS, demo_relabel_rows(), {"name": "换标单", "checksum": "x"})
        self.assertEqual(result["headline"], "截至 2026-11-21，有效工具 5 件，在管 4 件，借出 1 件。")


class RelabelClaimTest(unittest.TestCase):
    def test_cross_relabel_check_in_is_linked_back(self):
        result = analyze(relabeled_rows(), PARAMS, demo_relabel_rows(), {"name": "换标单", "checksum": "a" * 64})
        self.assertEqual(result["headline"], "截至 2026-11-21，有效工具 6 件，在管 5 件，借出 1 件。")
        assets = {row["asset_id"]: row for row in table(result, "期末资产快照")["rows"]}
        # 10-31 借出的 GQ-03，11-20 以新号 GQ-03B 归还，仍是同一件实物。
        self.assertEqual(assets["GQ-03B"]["alias_ids"], "GQ-03 → GQ-03B")
        self.assertEqual(assets["GQ-03B"]["status"], "在管")
        # 旧号 11-21 重新接收，发给另一件工具，另起一件实物。
        self.assertEqual(assets["GQ-03"]["alias_ids"], "GQ-03")
        claims = table(result, "换标认领依据")["rows"]
        self.assertEqual((claims[0]["state"], claims[0]["claimed_events"], claims[0]["claimed_check_ins"]),
                         ("已认领", 1, 1))

    def test_chained_relabels_continue_one_asset(self):
        result = analyze(
            rows(("A", "电钻", "2026-01-01", 1, "accept"), ("A", None, "2026-01-05", 1, "check-out"),
                 ("B", None, "2026-01-10", 1, "check-in"), ("B", None, "2026-01-12", 1, "check-out"),
                 ("C", None, "2026-01-20", 1, "check-in")),
            {"start_date": "2026-01-01", "as_of_date": "2026-02-28"},
            relabels(("R1", "A", "B", "2026-01-08", None), ("R2", "B", "C", "2026-01-18", None)),
            {"name": "换标单", "checksum": "b" * 64},
        )
        assets = table(result, "期末资产快照")["rows"]
        self.assertEqual(len(assets), 1)
        self.assertEqual((assets[0]["asset_id"], assets[0]["alias_ids"], assets[0]["status"]),
                         ("C", "A → B → C", "在管"))

    def test_relabel_on_same_day_as_handover(self):
        result = analyze(
            rows(("A", "电钻", "2026-01-01", 1, "accept"), ("A", None, "2026-01-05", 1, "check-out"),
                 ("A", None, "2026-01-10", 1, "check-in"), ("B", None, "2026-01-10", 2, "check-out"),
                 ("B", None, "2026-01-15", 1, "check-in")),
            {"start_date": "2026-01-01", "as_of_date": "2026-02-28"},
            relabels(("R1", "A", "B", "2026-01-10", None)),
            {"name": "换标单", "checksum": "c" * 64},
        )
        assets = table(result, "期末资产快照")["rows"]
        self.assertEqual([(row["asset_id"], row["status"]) for row in assets], [("B", "在管")])

    def test_old_id_reissued_to_another_tool(self):
        result = analyze(
            rows(("A", "电钻", "2026-01-01", 1, "accept"), ("A", None, "2026-01-10", 1, "check-out"),
                 ("B", None, "2026-01-15", 1, "check-in"), ("A", "切割机", "2026-01-20", 1, "accept")),
            {"start_date": "2026-01-01", "as_of_date": "2026-02-28"},
            relabels(("R1", "A", "B", "2026-01-10", None)),
            {"name": "换标单", "checksum": "d" * 64},
        )
        assets = {row["asset_id"]: row for row in table(result, "期末资产快照")["rows"]}
        self.assertEqual(assets["B"]["asset_name"], "电钻")
        self.assertEqual(assets["A"]["asset_name"], "切割机")
        self.assertEqual(len(assets), 2)


class RelabelConflictTest(unittest.TestCase):
    def _conflict(self):
        return analyze(
            rows(("A", "电钻", "2026-01-01", 1, "accept"), ("B", "切割机", "2026-01-01", 2, "accept"),
                 ("A", None, "2026-01-06", 1, "check-out"), ("B", None, "2026-01-07", 1, "check-out")),
            {"start_date": "2026-01-01", "as_of_date": "2026-02-28"},
            relabels(("R1", "A", "C", "2026-01-05", None), ("R2", "A", "D", "2026-01-20", None),
                     ("R3", "C", "E", "2026-02-01", "2026-03-01")),
            {"name": "换标单", "checksum": "e" * 64},
        )

    def test_conflicting_relabels_suspend_whole_history_of_involved_tools(self):
        result = self._conflict()
        self.assertEqual(sorted(result["relabel"]["suspended_tools"]), ["A", "C", "D", "E"])
        suspended = table(result, "挂起的交接单")["rows"]
        self.assertEqual({row["event_id"] for row in suspended}, {"E001", "E003"})
        # B 与冲突无关，照常盘点。
        assets = [row["asset_id"] for row in table(result, "期末资产快照")["rows"]]
        self.assertEqual(assets, ["B"])
        self.assertEqual(result["headline"], "截至 2026-02-28，有效工具 1 件，在管 0 件，借出 1 件。"
                                            "换标单生效期冲突，工具 A、C、D、E 的整段流转挂起，其余工具照常盘点。")
        self.assertEqual(result["state"], "warning")

    def test_conflicting_relabel_rows_are_marked_suspended(self):
        claims = {row["relabel_id"]: row for row in table(self._conflict(), "换标认领依据")["rows"]}
        self.assertEqual(claims["R1"]["state"], "挂起")
        self.assertEqual(claims["R3"]["state"], "挂起")
        self.assertIn("生效期重叠", claims["R1"]["note"])

    def test_chained_relabels_do_not_conflict(self):
        suspended, tools, _ = conflicts(ordered(relabels(("R1", "A", "B", "2026-01-05", None),
                                                         ("R2", "B", "C", "2026-01-10", None))))
        self.assertEqual((suspended, tools), (set(), set()))

    def test_same_day_chain_accepts_any_registration_order(self):
        result = analyze(
            rows(("A", "电钻", "2026-01-01", 1, "accept"), ("B", None, "2026-01-05", 1, "check-out"),
                 ("C", None, "2026-01-09", 1, "check-in")),
            {"start_date": "2026-01-01", "as_of_date": "2026-01-31"},
            relabels(("R2", "B", "C", "2026-01-05", None), ("R1", "A", "B", "2026-01-05", None)),
            {"name": "换标单", "checksum": "f" * 64},
        )
        assets = table(result, "期末资产快照")["rows"]
        self.assertEqual([(row["asset_id"], row["alias_ids"]) for row in assets], [("C", "A → B → C")])

    def test_same_target_overlap_conflicts(self):
        suspended, tools, _ = conflicts(ordered(relabels(
            ("R1", "A", "C", "2026-01-05", None), ("R2", "B", "C", "2026-01-06", None))))
        self.assertEqual(suspended, {0, 1})
        self.assertEqual(tools, {"A", "B", "C"})

    def test_duplicate_and_conflicting_sheets_are_rejected(self):
        self.assertEqual(len(ordered(relabels(("R1", "A", "B", "2026-01-05", None)) * 2)), 1)
        with self.assertRaises(ValueError):
            ordered(relabels(("R1", "A", "B", "2026-01-05", None), ("R1", "A", "C", "2026-01-05", None)))
        with self.assertRaises(ValueError):
            ordered(relabels(("R1", "A", "A", "2026-01-05", None)))
        with self.assertRaises(ValueError):
            ordered(relabels(("R1", "A", "B", "2026-01-09", "2026-01-08")))


if __name__ == "__main__":
    unittest.main()
