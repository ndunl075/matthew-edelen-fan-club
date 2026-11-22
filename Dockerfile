FROM golang:1.23-alpine AS build
WORKDIR /src
COPY go.mod ./
COPY *.go ./
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /bot .

FROM scratch
COPY --from=build /bot /bot
ENTRYPOINT ["/bot"]
