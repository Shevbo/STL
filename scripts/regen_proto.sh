#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROTO_ROOT="$REPO_ROOT/trader/proto"
GEN_ROOT="$REPO_ROOT/trader/proto/gen"

# Bundled google/protobuf protos shipped with grpcio-tools
GRPC_TOOLS_PROTO="$(python -c "
import grpc_tools, os
print(os.path.join(os.path.dirname(grpc_tools.__file__), '_proto'))
")"

rm -rf "$GEN_ROOT"
mkdir -p "$GEN_ROOT"

python -m grpc_tools.protoc \
  -I "$PROTO_ROOT" \
  -I "$GRPC_TOOLS_PROTO" \
  --python_out="$GEN_ROOT" \
  --grpc_python_out="$GEN_ROOT" \
  grpc/tradeapi/v1/marketdata/marketdata_service.proto \
  grpc/tradeapi/v1/side.proto \
  grpc/gateway/protoc_gen_openapiv2/options/annotations.proto \
  grpc/gateway/protoc_gen_openapiv2/options/openapiv2.proto \
  google/api/annotations.proto \
  google/api/http.proto \
  google/type/decimal.proto \
  google/type/interval.proto

# Add __init__.py to every generated package directory.
# Skip top-level namespace packages: google/ and grpc/ must NOT have __init__.py
# so Python's namespace-package mechanism merges them with the installed packages
# (google.protobuf from protobuf, and grpc.aio from grpcio).
while IFS= read -r -d '' d; do
  rel="${d#${GEN_ROOT}/}"
  case "$rel" in
    "" | "google" | "grpc") continue ;;
  esac
  touch "$d/__init__.py"
done < <(find "$GEN_ROOT" -type d -print0)

# Fix conflicting grpc.* imports in _pb2_grpc.py files
python "$REPO_ROOT/scripts/fix_grpc_imports.py" "$GEN_ROOT"

echo "Regen complete. Generated files in $GEN_ROOT"
