import hashlib

from src.models.entities import DatasetVersion
from src.services.specimen_csv import canonical_checksum
from src.utils import repository as repo


def _row_content_hash(row):
    return hashlib.sha256(
        f"{row['group']}|{float(row['duration_hours'])}|{int(row['event'])}".encode()
    ).hexdigest()[:16]


def upgrade_all():
    """为历史数据集建立不可变的第 1 版快照，并给历史运行补上资料版本指针。

    整个升级在同一个串行事务内完成，避免与并发补传互相覆盖。
    注意不能调用 latest_rows/dataset 服务，避免与建表阶段的循环导入。
    """
    with repo.transaction() as (conn, q):
        datasets = {}
        for dataset in repo.all_in(conn, q, 'datasets'):
            # 已升级资料只补运行指针，不再重复生成版本记录。
            if not dataset.get('latest_version_id') or dataset.get('row_history') is None:
                dataset['latest_version'] = 1
                dataset['latest_version_id'] = dataset['id'] + '-v1'
                dataset['latest_revision'] = 0
                dataset['version_count'] = 1
                dataset['batch_count'] = dataset.get('batch_count', 0)
                dataset['specimen_revisions'] = {r['specimen_id']: 0 for r in dataset['rows']}
                dataset['row_history'] = {r['specimen_id']: [_row_content_hash(r)] for r in dataset['rows']}
                dataset['withdrawn'] = dataset.get('withdrawn', [])
                dataset['updated_at'] = dataset.get('updated_at', dataset.get('created_at', ''))
                checksum = canonical_checksum(dataset['rows'], dataset['specimen_revisions'], dataset['withdrawn'])
                dataset['data_checksum'] = checksum
                version = DatasetVersion(
                    id=dataset['latest_version_id'], dataset_id=dataset['id'], version=1, revision=0,
                    rows=dataset['rows'], specimen_revisions=dataset['specimen_revisions'], withdrawn=[],
                    source=dataset.get('source', ''), batch_id='', checksum=checksum,
                    created_at=dataset.get('created_at', ''),
                )
                repo.put_new_in(conn, q, 'dataset_versions', version)
                repo.put_in(conn, q, 'datasets', dataset)
            datasets[dataset['id']] = dataset

        for run in repo.all_in(conn, q, 'runs'):
            if run.get('dataset_version_id'):
                continue
            study = repo.find_in(conn, q, 'studies', run['study_id'])
            dataset = datasets.get(study['dataset_id']) if study else None
            if not dataset:
                continue
            run['dataset_id'] = dataset['id']
            run['dataset_version_id'] = dataset.get('latest_version_id', '')
            run['dataset_version'] = dataset.get('latest_version', 1)
            run['dataset_revision'] = dataset.get('latest_revision', 0)
            run['dataset_checksum'] = dataset.get('data_checksum', '')
            repo.put_in(conn, q, 'runs', run)
