"""Consistent SQLite backup with immutable evidence and SHA256 manifest.

Restore writes a NEW directory only. It never replaces the running database.
PostgreSQL operators must use pg_dump + private object storage snapshot; see guide.
"""
import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()

def inside(root, relative):
    target=(root/relative).resolve()
    if not target.is_relative_to(root.resolve()): raise ValueError('Path escapes backup directory')
    return target

def verify(root):
    manifest=json.loads((root/'manifest.json').read_text('utf-8'))
    if manifest.get('format')!='inspection-sqlite-v1': raise ValueError('Unknown backup format')
    for rel, sha in manifest['files'].items():
        file=inside(root,rel)
        if not file.is_file() or digest(file)!=sha: raise ValueError('Backup checksum mismatch: '+rel)
    with sqlite3.connect((root/'app.db').as_uri()+'?mode=ro',uri=True) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('Database integrity error')
        if db.execute('PRAGMA foreign_key_check').fetchall(): raise ValueError('Database foreign key error')
        for raw,file in db.execute('SELECT raw_key,file_key FROM attachments WHERE deleted_at IS NULL'):
            for key in {raw,file}:
                if key and 'storage/'+key not in manifest['files']: raise ValueError('Missing evidence in manifest')
    return manifest

def backup(target):
    from app.config import settings
    from app.services.storage import get_storage
    from sqlalchemy.engine import make_url
    url=make_url(settings.db_url)
    if url.get_backend_name()!='sqlite':
        raise ValueError('This command handles SQLite only. Use PostgreSQL pg_dump and object snapshot procedure in the guide.')
    source=Path(url.database).resolve()
    if target.exists(): raise ValueError('Destination already exists; choose a new backup directory')
    target.mkdir(parents=True)
    with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as src, sqlite3.connect(target/'app.db') as dst:
        src.backup(dst)
    storage=get_storage()
    with sqlite3.connect(target/'app.db') as db:
        keys={key for row in db.execute('SELECT raw_key,file_key FROM attachments WHERE deleted_at IS NULL') for key in row if key}
    for key in keys:
        dest=inside(target/'storage',key);dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_bytes(storage.get(key))
    files={str(f.relative_to(target)).replace('\\','/'):digest(f) for f in target.rglob('*') if f.is_file()}
    manifest={'format':'inspection-sqlite-v1','created_at':datetime.now(timezone.utc).isoformat(),'files':files}
    (target/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),'utf-8')
    verify(target)
    return {'verified':True,'files':len(files),'destination':str(target)}

def restore(source,target):
    verify(source)
    if target.exists(): raise ValueError('Restore target must not exist; overwriting a live system is forbidden')
    shutil.copytree(source,target)
    verify(target)
    return {'verified':True,'destination':str(target),'next':'Configure DATABASE_URL and STORAGE_LOCAL_DIR to this NEW directory, then run migrations and readiness checks before switching traffic.'}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['backup','verify','restore'])
    parser.add_argument('path',type=Path)
    parser.add_argument('--target',type=Path)
    args=parser.parse_args()
    if args.action=='backup': result=backup(args.path.resolve())
    elif args.action=='verify': result={'verified':bool(verify(args.path.resolve()))}
    else:
        if args.target is None: parser.error('restore requires --target')
        result=restore(args.path.resolve(),args.target.resolve())
    print(json.dumps(result,ensure_ascii=False))
