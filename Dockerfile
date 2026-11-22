FROM golang:1.25.4-alpine3.22@sha256:d3f0cf7723f3429e3f9ed846243970b20a2de7bae6a5b66fc5914e228d831bbb AS gobuild
ENV CGO_ENABLED=0 GOOS=linux GOARCH=amd64 GOFLAGS=-mod=mod GOTOOLCHAIN=local
WORKDIR /az
COPY third_party/azing/go.mod ./
COPY third_party/azing/cmd ./cmd
COPY third_party/azing/internal ./internal
RUN go build -trimpath -ldflags="-s -w" -o /out/az ./cmd/bot
WORKDIR /launch
COPY launch ./
RUN go build -trimpath -ldflags="-s -w" -o /out/launch .

FROM rust:1.95@sha256:f49565f188ee00bc2a18dd418183f2c5f23ef7d6e691890517ed341a598f67c3 AS build
WORKDIR /src
COPY Cargo.toml Cargo.lock ./
COPY src ./src
RUN cargo build --release --locked \
 && install -m 0755 target/release/glen /glen

FROM debian:trixie-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2
COPY --from=build --chmod=0755 /glen /usr/local/bin/glen
COPY --from=gobuild --chmod=0755 /out/az /usr/local/bin/az
COPY --from=gobuild --chmod=0755 /out/launch /usr/local/bin/launch
COPY --chmod=0644 bot.json /bot.json
ENV MECH_PARAMS=/bot.json
USER 65534:65534
WORKDIR /tmp
ENTRYPOINT ["/usr/local/bin/launch"]
