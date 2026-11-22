from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from src.config.database import connection
from src.constants.enums import Role
from src.controllers.auth import allow, current
from src.domain.spec import SPEC
from src.models.identity import User
from src.models.source import Dataset, RelabelSheet
from src.schemas.requests import Upload
from src.services.audit import record
from src.services.datasets import relabel_template, save, save_relabels, template
from src.services.validation import parse_relabel_rows, parse_rows
from src.utils import views

router = APIRouter()


@router.get('/spec')
def spec():
    return {key: value for key, value in SPEC.items() if key != 'fixture'}


@router.get('/template')
def sample(actor: User = Depends(current)):
    filename = quote(SPEC['sample_name'])
    return Response(template(), media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f"attachment; filename*=UTF-8''{filename}"})


@router.get('/datasets')
def datasets(db: Session = Depends(connection), actor: User = Depends(current)):
    return [views.dataset(item) for item in db.scalars(select(Dataset).order_by(Dataset.id.desc()).limit(100))]


@router.get('/datasets/{dataset_id}')
def details(dataset_id: int, offset: int = Query(0, ge=0), limit: int = Query(16, ge=1, le=100),
            db: Session = Depends(connection), actor: User = Depends(current)):
    item = db.get(Dataset, dataset_id)
    if not item:
        raise HTTPException(404, '找不到该数据批次')
    return {**views.dataset(item), 'rows': item.rows[offset:offset + limit]}


@router.post('/datasets', status_code=201)
def upload(body: Upload, db: Session = Depends(connection),
           actor: User = Depends(allow(Role.ADMIN, Role.ANALYST))):
    rows = parse_rows(body.csv_text)
    item, created = save(db, body.name, body.filename, rows, actor.username)
    if created:
        record(db, actor.username, 'dataset.imported', item.id, rows=item.row_count)
    db.commit()
    return {**views.dataset(item), 'created': created}


@router.get('/relabels/template')
def relabel_sample(actor: User = Depends(current)):
    filename = quote(SPEC['relabel']['sample_name'])
    return Response(relabel_template(), media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f"attachment; filename*=UTF-8''{filename}"})


@router.get('/relabels')
def relabel_sheets(db: Session = Depends(connection), actor: User = Depends(current)):
    return [views.relabel_sheet(item) for item in
            db.scalars(select(RelabelSheet).order_by(RelabelSheet.name, RelabelSheet.version.desc()).limit(100))]


@router.get('/relabels/{sheet_id}')
def relabel_details(sheet_id: int, db: Session = Depends(connection), actor: User = Depends(current)):
    item = db.get(RelabelSheet, sheet_id)
    if not item:
        raise HTTPException(404, '找不到该换标单版本')
    return views.relabel_sheet(item, detail=True)


@router.post('/relabels', status_code=201)
def relabel_upload(body: Upload, db: Session = Depends(connection),
                   actor: User = Depends(allow(Role.ADMIN, Role.ANALYST))):
    rows = parse_relabel_rows(body.csv_text)
    sheet, created = save_relabels(db, body.name, body.filename, rows, actor.username)
    if created:
        record(db, actor.username, 'relabel.imported', sheet.id, version=sheet.version, rows=sheet.row_count)
    db.commit()
    return {**views.relabel_sheet(sheet), 'created': created}
