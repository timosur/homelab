# Build stage
FROM golang:1.21-alpine AS builder

WORKDIR /app

# Install git for go mod download
RUN apk add --no-cache git

# Copy go mod files
COPY go.mod go.sum* ./
RUN go mod download

# Copy source code
COPY *.go ./

# Build binary
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-w -s" -o wol-proxy .

# Runtime stage
FROM alpine:3.19

# Install SSH client for suspend functionality
RUN apk add --no-cache openssh-client ca-certificates

WORKDIR /app

# Copy binary from builder
COPY --from=builder /app/wol-proxy /app/wol-proxy

# Create non-root user
RUN adduser -D -u 1000 appuser
USER appuser

ENTRYPOINT ["/app/wol-proxy"]
CMD ["-config", "/config/config.yaml"]
