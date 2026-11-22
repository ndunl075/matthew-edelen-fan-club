# Build: docker buildx build --platform linux/amd64 --load -t my-bot .
FROM --platform=$BUILDPLATFORM golang:1.23-alpine AS build
ARG TARGETARCH
WORKDIR /src
COPY go.mod ./
COPY *.go ./
RUN CGO_ENABLED=0 GOOS=linux GOARCH=$TARGETARCH go build -trimpath -ldflags="-s -w" -o /out/bot .

FROM scratch
COPY --from=build /out/bot /bot
USER 65534:65534
ENTRYPOINT ["/bot"]
