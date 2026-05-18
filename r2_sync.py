"""
Cloudflare R2 sync helper.

R2 は S3 互換なので boto3 を使う。

Env vars:
  R2_ACCOUNT_ID
  R2_ACCESS_KEY_ID
  R2_SECRET_ACCESS_KEY
  R2_BUCKET

使い方:
  python r2_sync.py download xbrl_store fixed_assets_store land_price_data
  python r2_sync.py upload-all xbrl_store
  python r2_sync.py upload-changed xbrl_store fixed_assets_store --since 86400
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:
    import boto3
    from botocore.config import Config
except ImportError:
    print("boto3 not installed. pip install boto3", file=sys.stderr)
    sys.exit(1)


def get_client():
    account = os.environ["R2_ACCOUNT_ID"]
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        config=Config(retries={"max_attempts": 5, "mode": "standard"}),
        region_name="auto",
    )


def get_bucket():
    return os.environ["R2_BUCKET"]


def list_remote_objects(client, bucket: str, prefix: str) -> dict[str, dict]:
    """Return {key: {size, etag, last_modified}} for all objects under prefix."""
    paginator = client.get_paginator("list_objects_v2")
    out = {}
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            out[obj["Key"]] = {
                "size": obj["Size"],
                "etag": obj["ETag"].strip('"'),
                "last_modified": obj["LastModified"],
            }
    return out


def upload_file(client, bucket: str, local_path: Path, key: str):
    client.upload_file(str(local_path), bucket, key)


def download_file(client, bucket: str, key: str, local_path: Path):
    local_path.parent.mkdir(parents=True, exist_ok=True)
    client.download_file(bucket, key, str(local_path))


def file_md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_download(args):
    client = get_client()
    bucket = get_bucket()
    base = Path(args.base or ".")

    total_dl = 0
    total_skip = 0
    for prefix in args.prefixes:
        remote = list_remote_objects(client, bucket, prefix + "/")
        print(f"  {prefix}: {len(remote)} remote objects")

        tasks = []
        for key, meta in remote.items():
            local = base / key
            if local.exists() and local.stat().st_size == meta["size"]:
                total_skip += 1
                continue
            tasks.append((key, local, meta))

        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = [ex.submit(download_file, client, bucket, k, l) for k, l, _ in tasks]
            for i, f in enumerate(as_completed(futs)):
                f.result()
                if (i + 1) % 200 == 0:
                    print(f"    {i + 1}/{len(tasks)} downloaded")
        total_dl += len(tasks)
        print(f"  {prefix}: {len(tasks)} downloaded, {len(remote) - len(tasks)} skipped")

    print(f"Total: {total_dl} downloaded, {total_skip} skipped")


def cmd_upload_all(args):
    client = get_client()
    bucket = get_bucket()
    base = Path(args.base or ".")

    total_up = 0
    for prefix in args.prefixes:
        local_root = base / prefix
        if not local_root.exists():
            print(f"  {prefix}: local not found, skip")
            continue

        files = [p for p in local_root.rglob("*") if p.is_file()]
        print(f"  {prefix}: {len(files)} local files")

        tasks = []
        for fp in files:
            key = fp.relative_to(base).as_posix()
            tasks.append((fp, key))

        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = [ex.submit(upload_file, client, bucket, fp, k) for fp, k in tasks]
            for i, f in enumerate(as_completed(futs)):
                f.result()
                if (i + 1) % 200 == 0:
                    print(f"    {i + 1}/{len(tasks)} uploaded")
        total_up += len(files)
        print(f"  {prefix}: {len(files)} uploaded")

    print(f"Total: {total_up} uploaded")


def cmd_upload_changed(args):
    """Upload only files modified in the last N seconds (default 7200 = 2h)."""
    client = get_client()
    bucket = get_bucket()
    base = Path(args.base or ".")
    cutoff = time.time() - args.since

    total_up = 0
    for prefix in args.prefixes:
        local_root = base / prefix
        if not local_root.exists():
            continue

        changed = [p for p in local_root.rglob("*") if p.is_file() and p.stat().st_mtime >= cutoff]
        print(f"  {prefix}: {len(changed)} changed in last {args.since}s")

        tasks = [(fp, fp.relative_to(base).as_posix()) for fp in changed]
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = [ex.submit(upload_file, client, bucket, fp, k) for fp, k in tasks]
            for f in as_completed(futs):
                f.result()
        total_up += len(changed)

    print(f"Total: {total_up} uploaded")


def main():
    parser = argparse.ArgumentParser(description="Cloudflare R2 sync helper")
    parser.add_argument("--base", default=None, help="Local base directory (default: cwd)")
    parser.add_argument("--workers", type=int, default=16, help="Concurrent upload/download workers")

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_dl = sub.add_parser("download", help="Download all remote objects under given prefixes")
    p_dl.add_argument("prefixes", nargs="+")
    p_dl.set_defaults(func=cmd_download)

    p_up = sub.add_parser("upload-all", help="Upload all local files under given prefixes")
    p_up.add_argument("prefixes", nargs="+")
    p_up.set_defaults(func=cmd_upload_all)

    p_uc = sub.add_parser("upload-changed", help="Upload files modified within --since seconds")
    p_uc.add_argument("prefixes", nargs="+")
    p_uc.add_argument("--since", type=int, default=7200, help="Seconds (default 7200=2h)")
    p_uc.set_defaults(func=cmd_upload_changed)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
