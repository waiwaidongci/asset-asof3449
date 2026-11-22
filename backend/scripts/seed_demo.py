import json
from sqlalchemy import func, select
from src.config.database import Session
from src.constants.enums import Role
from src.domain.demo import demo_relabel_rows, demo_rows, relabeled_rows
from src.domain.spec import SPEC
from src.models.identity import User
from src.models.source import Dataset, RelabelSheet
from src.services.datasets import save, save_relabels
from src.utils.security import hash_password


def seed():
    with Session.begin() as db:
        for role in Role:
            if not db.get(User, role.value):
                db.add(User(username=role.value, role=role, password_hash=hash_password(f'{role.value}-demo-2611')))
        # 换标后的批次先入库（编号较小），列表默认展示旧批次：未交换标单也能照常盘点。
        relabeled, relabeled_created = save(
            db, f"{SPEC['default_dataset']}（换标后）", '桂桥工具流转_20261121_换标后.csv',
            relabeled_rows(), 'seed')
        item, created = save(db, SPEC['default_dataset'], SPEC['sample_name'], demo_rows(), 'seed')
        sheet, sheet_created = save_relabels(
            db, SPEC['relabel']['default_dataset'], SPEC['relabel']['sample_name'],
            demo_relabel_rows(), 'seed')
        return {'users': db.scalar(select(func.count()).select_from(User)),
                'datasets': db.scalar(select(func.count()).select_from(Dataset)),
                'relabel_sheets': db.scalar(select(func.count()).select_from(RelabelSheet)),
                'seed_dataset_id': item.id, 'row_count': item.row_count, 'checksum': item.checksum,
                'created_dataset': created,
                'relabeled_dataset_id': relabeled.id, 'relabeled_row_count': relabeled.row_count,
                'created_relabeled_dataset': relabeled_created,
                'seed_relabel_sheet_id': sheet.id, 'relabel_version': sheet.version,
                'created_relabel_sheet': sheet_created}


if __name__ == '__main__':
    print(json.dumps(seed(), ensure_ascii=False))
