ARG GO_VERSION
FROM golang:${GO_VERSION}-alpine AS build
WORKDIR /src
COPY go.mod ./
COPY *.go ./
ARG VERSION=dev
ARG COMMIT=unknown
RUN CGO_ENABLED=0 go build -trimpath -ldflags "-s -w -X main.version=${VERSION} -X main.commit=${COMMIT}" -o /payment-api .

FROM scratch
COPY --from=build /payment-api /payment-api
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/payment-api"]
