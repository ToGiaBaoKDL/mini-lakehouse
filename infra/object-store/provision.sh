#!/bin/sh
set -eu

action="${1:-}"
case "$action" in
  provision|verify) ;;
  *)
    printf '%s\n' "Usage: provision.sh <provision|verify>" >&2
    exit 2
    ;;
esac

read_secret() {
  secret_path="/run/secrets/$1"
  if [ ! -s "$secret_path" ]; then
    printf 'Missing or empty Docker secret: %s\n' "$1" >&2
    exit 2
  fi
  tr -d '\n' <"$secret_path"
}

MINIO_ROOT_PASSWORD="$(read_secret object_store_root_password)"
OBJECT_STORE_POLARIS_SECRET_KEY="$(read_secret object_store_polaris_secret_key)"
OBJECT_STORE_PLATFORM_ADMIN_SECRET_KEY="$(read_secret object_store_platform_admin_secret_key)"
OBJECT_STORE_PREFECT_INGESTION_SECRET_KEY="$(
  read_secret object_store_prefect_ingestion_secret_key
)"
OBJECT_STORE_TRINO_ENGINE_SECRET_KEY="$(read_secret object_store_trino_engine_secret_key)"
OBJECT_STORE_OCR_REVIEW_SECRET_KEY="$(read_secret object_store_ocr_review_secret_key)"

mc alias set local "$OBJECT_STORE_ENDPOINT" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
mc license info local >/dev/null

seen_access_keys=" $MINIO_ROOT_USER "
for access_key in \
  "$OBJECT_STORE_POLARIS_ACCESS_KEY" \
  "$OBJECT_STORE_PLATFORM_ADMIN_ACCESS_KEY" \
  "$OBJECT_STORE_PREFECT_INGESTION_ACCESS_KEY" \
  "$OBJECT_STORE_TRINO_ENGINE_ACCESS_KEY" \
  "$OBJECT_STORE_OCR_REVIEW_ACCESS_KEY"; do
  case "$seen_access_keys" in
    *" $access_key "*)
      printf 'Object-store access keys must be unique and cannot use the root identity: %s\n' \
        "$access_key" >&2
      exit 2
      ;;
  esac
  seen_access_keys="${seen_access_keys}${access_key} "
done

seen_buckets=" "
for uri in "$LANDING_URI" "$CURATED_URI" "$ANALYTICS_URI"; do
  case "$uri" in
    s3://?*) ;;
    *)
      printf 'Invalid lifecycle S3 URI: %s\n' "$uri" >&2
      exit 2
      ;;
  esac
  bucket_path="${uri#s3://}"
  bucket="${bucket_path%%/*}"
  if [ "$bucket_path" != "$bucket" ]; then
    printf 'Local AIStor lifecycle URI must identify a bucket root: %s\n' "$uri" >&2
    exit 2
  fi
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

landing_bucket="${LANDING_URI#s3://}"
curated_bucket="${CURATED_URI#s3://}"
analytics_bucket="${ANALYTICS_URI#s3://}"
if [ "$landing_bucket" = "$curated_bucket" ] \
  || [ "$landing_bucket" = "$analytics_bucket" ] \
  || [ "$curated_bucket" = "$analytics_bucket" ]; then
  printf '%s\n' "Local AIStor lifecycle buckets must be distinct for workload IAM isolation" >&2
  exit 2
fi

if [ "$action" = "provision" ]; then
  policy_dir="$(mktemp -d)"
  trap 'rm -rf "$policy_dir"' EXIT

  cat >"$policy_dir/all-readwrite.json" <<EOF
{"Version":"2012-10-17","Statement":[
  {"Effect":"Allow","Action":["s3:GetBucketLocation","s3:ListBucket","s3:ListBucketMultipartUploads"],"Resource":["arn:aws:s3:::$landing_bucket","arn:aws:s3:::$curated_bucket","arn:aws:s3:::$analytics_bucket"]},
  {"Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:DeleteObject","s3:AbortMultipartUpload","s3:ListMultipartUploadParts"],"Resource":["arn:aws:s3:::$landing_bucket/*","arn:aws:s3:::$curated_bucket/*","arn:aws:s3:::$analytics_bucket/*"]}
]}
EOF
  cat >"$policy_dir/orchestration.json" <<EOF
{"Version":"2012-10-17","Statement":[
  {"Effect":"Allow","Action":["s3:GetBucketLocation","s3:ListBucket","s3:ListBucketMultipartUploads"],"Resource":["arn:aws:s3:::$landing_bucket","arn:aws:s3:::$curated_bucket","arn:aws:s3:::$analytics_bucket"]},
  {"Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:DeleteObject","s3:AbortMultipartUpload","s3:ListMultipartUploadParts"],"Resource":["arn:aws:s3:::$landing_bucket/*","arn:aws:s3:::$curated_bucket/*"]},
  {"Effect":"Allow","Action":["s3:GetObject"],"Resource":["arn:aws:s3:::$analytics_bucket/*"]}
]}
EOF
  cat >"$policy_dir/curated-readonly.json" <<EOF
{"Version":"2012-10-17","Statement":[
  {"Effect":"Allow","Action":["s3:GetBucketLocation","s3:ListBucket"],"Resource":["arn:aws:s3:::$curated_bucket"]},
  {"Effect":"Allow","Action":["s3:GetObject"],"Resource":["arn:aws:s3:::$curated_bucket/*"]}
]}
EOF

  mc admin policy create local lakehouse-all-readwrite "$policy_dir/all-readwrite.json" >/dev/null
  mc admin policy create local lakehouse-orchestration "$policy_dir/orchestration.json" >/dev/null
  mc admin policy create \
    local lakehouse-curated-readonly "$policy_dir/curated-readonly.json" >/dev/null

else
  for policy in \
    lakehouse-all-readwrite \
    lakehouse-orchestration \
    lakehouse-curated-readonly; do
    mc admin policy info local "$policy" >/dev/null
  done
fi

manage_user() {
  access_key="$1"
  secret_key="$2"
  policy="$3"
  allowed_buckets="$4"
  denied_buckets="$5"

  if [ "$action" = "provision" ]; then
    mc admin user add local "$access_key" "$secret_key" >/dev/null
    mc admin policy attach local "$policy" --user "$access_key" >/dev/null
  else
    mc admin user info local "$access_key" >/dev/null
  fi

  alias="workload-$access_key"
  mc alias set "$alias" "$OBJECT_STORE_ENDPOINT" "$access_key" "$secret_key" >/dev/null
  for bucket in $allowed_buckets; do
    mc stat "$alias/$bucket" >/dev/null
  done
  for bucket in $denied_buckets; do
    if mc stat "$alias/$bucket" >/dev/null 2>&1; then
      printf 'Object-store identity %s has unexpected access to bucket %s\n' \
        "$access_key" "$bucket" >&2
      exit 1
    fi
  done
}

all_buckets="$landing_bucket $curated_bucket $analytics_bucket"
manage_user \
  "$OBJECT_STORE_POLARIS_ACCESS_KEY" \
  "$OBJECT_STORE_POLARIS_SECRET_KEY" \
  lakehouse-all-readwrite \
  "$all_buckets" \
  ""
manage_user \
  "$OBJECT_STORE_PLATFORM_ADMIN_ACCESS_KEY" \
  "$OBJECT_STORE_PLATFORM_ADMIN_SECRET_KEY" \
  lakehouse-all-readwrite \
  "$all_buckets" \
  ""
manage_user \
  "$OBJECT_STORE_PREFECT_INGESTION_ACCESS_KEY" \
  "$OBJECT_STORE_PREFECT_INGESTION_SECRET_KEY" \
  lakehouse-orchestration \
  "$all_buckets" \
  ""
manage_user \
  "$OBJECT_STORE_TRINO_ENGINE_ACCESS_KEY" \
  "$OBJECT_STORE_TRINO_ENGINE_SECRET_KEY" \
  lakehouse-all-readwrite \
  "$all_buckets" \
  ""
manage_user \
  "$OBJECT_STORE_OCR_REVIEW_ACCESS_KEY" \
  "$OBJECT_STORE_OCR_REVIEW_SECRET_KEY" \
  lakehouse-curated-readonly \
  "$curated_bucket" \
  "$landing_bucket $analytics_bucket"
