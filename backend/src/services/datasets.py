import csv
import hashlib
import io
import json
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from src.domain.demo import demo_rows, demo_relabel_rows
from src.domain.spec import SPEC
from src.models.source import Dataset, RelabelSheet


def checksum(rows):
    raw = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def save(db, name, filename, rows, actor):
    signature = checksum(rows)
    lookup = select(Dataset).where(Dataset.name == name, Dataset.checksum == signature)
    existing = db.scalar(lookup)
    if existing:
        return existing, False
    dataset = Dataset(name=name, filename=filename, checksum=signature, rows=rows,
                      row_count=len(rows), author=actor)
    try:
        with db.begin_nested():
            db.add(dataset)
            db.flush()
    except IntegrityError:
        existing = db.scalar(lookup.with_for_update())
        if existing:
            return existing, False
        raise
    return dataset, True


def save_relabels(db, name, filename, rows, actor):
    """\u540c\u540d\u6362\u6807\u5355\u5185\u5bb9\u53d8\u5316\u5373\u751f\u6210\u65b0\u7248\u672c\uff1b\u91cd\u590d\u4e0a\u4f20\u540c\u4e00\u5185\u5bb9\u8fd4\u56de\u539f\u7248\u672c\u3002"""
    signature = checksum(rows)
    lookup = select(RelabelSheet).where(RelabelSheet.name == name, RelabelSheet.checksum == signature)
    existing = db.scalar(lookup)
    if existing:
        return existing, False
    for _ in range(3):
        version = (db.scalar(select(func.max(RelabelSheet.version))
                             .where(RelabelSheet.name == name)) or 0) + 1
        sheet = RelabelSheet(name=name, filename=filename, checksum=signature, version=version,
                             rows=rows, row_count=len(rows), author=actor)
        try:
            with db.begin_nested():
                db.add(sheet)
                db.flush()
        except IntegrityError:
            # \u5e76\u53d1\u8865\u6b63\u540c\u4e00\u6362\u6807\u5355\u65f6\u91cd\u8bfb\uff1b\u5185\u5bb9\u76f8\u540c\u8fd4\u56de\u5df2\u6709\u7248\u672c\uff0c\u5426\u5219\u5360\u7528\u4e0b\u4e00\u4e2a\u7248\u672c\u53f7\u3002
            existing = db.scalar(lookup.with_for_update())
            if existing:
                return existing, False
            continue
        return sheet, True
    raise ValueError('\u6362\u6807\u5355\u7248\u672c\u6b63\u5728\u5e76\u53d1\u8865\u6b63\uff0c\u8bf7\u7a0d\u540e\u91cd\u8bd5')


def template():
    fields = [field['key'] for field in SPEC['fields']]
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    # The complete representative dataset also works as a reproducible import fixture.
    writer.writerows(demo_rows())
    return '\ufeff' + buffer.getvalue()


def relabel_template():
    fields = [field['key'] for field in SPEC['relabel']['fields']]
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(demo_relabel_rows())
    return '\ufeff' + buffer.getvalue()
