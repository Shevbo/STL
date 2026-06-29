# Single dev runner for Shectory Trade & Lab (QUIK agent + STL).
# Mirror of dev.ps1 for bash / hoster / CI. Same verbs, same codegen flags.
# Tools are NOT auto-installed; missing ones fail with a hint.

PROTO       := shectory/quik/v1/quik_agent.proto
GO_MAP      := Mshectory/quik/v1/quik_agent.proto=shectory/quik_agent/internal/pb
BUILD_REV   := $(shell git rev-parse --short HEAD 2>/dev/null || echo dev)
PYTEST_ENV  := FINAM_SECRET_TOKEN=$${FINAM_SECRET_TOKEN:-dummy}

.DEFAULT_GOAL := help
.PHONY: help gen gen-go gen-py tidy build test test-go test-py lint check clean all

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | sort | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  %-10s %s\n", $$1, $$2}'

gen: gen-go gen-py ## Codegen Go + Python stubs

gen-go: ## Codegen proto -> quik_agent/internal/pb (package quikv1)
	@command -v protoc >/dev/null || { echo "missing protoc -> https://github.com/protocolbuffers/protobuf/releases"; exit 1; }
	@command -v protoc-gen-go >/dev/null || { echo "missing protoc-gen-go -> go install google.golang.org/protobuf/cmd/protoc-gen-go@latest"; exit 1; }
	@command -v protoc-gen-go-grpc >/dev/null || { echo "missing protoc-gen-go-grpc -> go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@latest"; exit 1; }
	cd quik_agent && mkdir -p internal/pb && protoc -I ../proto \
	  --go_out=. --go_opt=module=shectory/quik_agent --go_opt=$(GO_MAP) \
	  --go-grpc_out=. --go-grpc_opt=module=shectory/quik_agent --go-grpc_opt=$(GO_MAP) --go-grpc_opt=require_unimplemented_servers=false \
	  ../proto/$(PROTO)

gen-py: ## Codegen proto -> trader/quik/pb
	@python -c "import grpc_tools" 2>/dev/null || { echo "missing grpcio-tools -> python -m pip install grpcio-tools"; exit 1; }
	mkdir -p trader/quik/pb && python -m grpc_tools.protoc -Iproto \
	  --python_out=trader/quik/pb --grpc_python_out=trader/quik/pb proto/$(PROTO)

tidy: ## go mod tidy in quik_agent
	cd quik_agent && go mod tidy

build: ## Build agent exe (amd64 + 386) into quik_agent/dist
	cd quik_agent && mkdir -p dist && \
	  GOOS=windows GOARCH=amd64 go build -ldflags "-X main.agentBuildRevStr=$(BUILD_REV)" -o dist/quik-agent_amd64.exe ./cmd/quik-agent && \
	  GOOS=windows GOARCH=386   go build -ldflags "-X main.agentBuildRevStr=$(BUILD_REV)" -o dist/quik-agent.exe ./cmd/quik-agent
	@echo "built quik_agent/dist (rev $(BUILD_REV))"

test: test-go test-py ## Run all tests

test-go: ## go test ./... in quik_agent
	cd quik_agent && go test ./...

test-py: ## pytest -m "not integration"
	$(PYTEST_ENV) python -m pytest -m "not integration" -q

lint: ## gofmt + go vet + ruff
	python -m ruff check trader/ tests/
	@if command -v go >/dev/null; then \
	  cd quik_agent && bad=$$(gofmt -l .); \
	  if [ -n "$$bad" ]; then echo "gofmt needs: $$bad"; exit 1; fi; \
	  go vet ./...; \
	else echo "go not installed -> skipped gofmt/vet"; fi

check: ## Report installed toolchain (no install)
	@for t in python ruff go protoc protoc-gen-go protoc-gen-go-grpc git; do \
	  if command -v $$t >/dev/null; then echo "  OK   $$t"; else echo "  MISS $$t"; fi; done
	@python -c "import grpc_tools" 2>/dev/null && echo "  OK   grpcio-tools" || echo "  MISS grpcio-tools"

clean: ## Remove generated stubs + built exes
	rm -rf quik_agent/dist quik_agent/internal/pb trader/quik/pb/shectory
	@echo "cleaned generated stubs + dist"

all: gen tidy build test lint ## Full pipeline
