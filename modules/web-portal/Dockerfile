ARG NODE_VERSION
ARG GO_VERSION
FROM node:${NODE_VERSION}-alpine AS frontend
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY tsconfig.json index.html ./
COPY src ./src
ARG VERSION=dev
ARG COMMIT=unknown
RUN VITE_APP_VERSION="${VERSION}" VITE_APP_COMMIT="${COMMIT}" npm run build

FROM golang:${GO_VERSION}-alpine AS server
WORKDIR /src
COPY go.mod ./
COPY *.go ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /web-portal .

FROM scratch
WORKDIR /app
COPY --from=frontend /src/dist ./dist
COPY --from=server /web-portal /web-portal
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/web-portal"]
