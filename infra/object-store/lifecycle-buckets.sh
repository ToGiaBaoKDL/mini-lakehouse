#!/bin/sh
set -eu

action="${1:-}"
case "$action" in
  provision|verify) ;;
  *)
    printf '%s\n' "Usage: lifecycle-buckets.sh <provision|verify>" >&2
    exit 2
    ;;
esac

mc alias set local "$OBJECT_STORE_ENDPOINT" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
mc license info local >/dev/null

seen_buckets=" "
for uri in "$LANDING_URI" "$CURATED_URI" "$ANALYTICS_URI"; do
  case "$uri" in
    s3://?*) ;;
    *)
      printf 'Invalid lifecycle S3 URI: %s\n' "$uri" >&2
      exit 2
      ;;
  esac
  bucket="${uri#s3://}"
  bucket="${bucket%%/*}"
  case "$seen_buckets" in
    *" $bucket "*) continue ;;
  esac
  seen_buckets="${seen_buckets}${bucket} "
  if [ "$action" = "provision" ]; then
    if ! mc stat "local/$bucket" >/dev/null 2>&1; then
      mc mb --ignore-existing "local/$bucket"
    fi
  else
    mc stat "local/$bucket" >/dev/null
  fi
done
