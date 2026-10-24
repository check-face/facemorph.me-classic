# Frozen inputs: every base image is pinned by digest (resolved 2026-10-24), Python dependencies are
# hash-locked per torch index (requirements-<index>.lock), the NVlabs source is fetched by commit and
# SHA-256, and the frontend is the prebuilt bundle in frontend/ checked against its SHA-256.
# ../scripts/check-pins.py fails on any floating base image.
FROM --platform=$BUILDPLATFORM node:22-bookworm-slim@sha256:de170b39439eb0e5a3444ad7d60b015614a381e10fecae2e30ec9e735122f38d AS node-tools

# Opt-in only (`docker build --target runtime-from-source`): rebuild the bundle from the vendored source
# at 0abb215 plus the overlay, with the end-of-life .NET 5 / Fable 3.1.12 toolchain.
FROM --platform=$BUILDPLATFORM mcr.microsoft.com/dotnet/sdk:5.0.408-bullseye-slim@sha256:40c6bd0059eaa06b4a9c91cd3e6df138f6224bd02b2882bf6ce3aa4af3835fc5 AS frontend-source
COPY --from=node-tools /usr/local/ /usr/local/
ENV DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1
WORKDIR /src
COPY frontend/source/ ./
COPY frontend-overlay/source/ ./
RUN npm ci && npm run build:self-host

# Default: the prebuilt bundle, rejected unless it matches its pinned SHA-256.
FROM python:3.11-slim-bookworm@sha256:f06d01d4ba3f8ad5af45a3ec87df19c1d317d8e8961edc0fabda61811af6f7cf AS frontend-bundle
COPY frontend/deploy-self-host.tar.gz frontend/deploy-self-host.tar.gz.sha256 /tmp/
RUN cd /tmp && sha256sum -c deploy-self-host.tar.gz.sha256 && mkdir /deploy-self-host && tar -xzf deploy-self-host.tar.gz -C /deploy-self-host

FROM python:3.11-slim-bookworm@sha256:f06d01d4ba3f8ad5af45a3ec87df19c1d317d8e8961edc0fabda61811af6f7cf AS runtime-deps
ARG STYLEGAN_REV=d72cc7d041b42ec8e806021a205ed9349f87c6a4
ARG STYLEGAN_SHA256=e09a4f09499bad8019d87b18b6ce19c91c0ae6d82e8a7af1fc16b22b4f9ebbba
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates ffmpeg build-essential cmake && rm -rf /var/lib/apt/lists/*
RUN python -c "import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], '/tmp/stylegan.tar.gz')" "https://github.com/NVlabs/stylegan2-ada-pytorch/archive/${STYLEGAN_REV}.tar.gz" \
 && echo "${STYLEGAN_SHA256}  /tmp/stylegan.tar.gz" | sha256sum -c - \
 && mkdir /opt/stylegan && tar -xzf /tmp/stylegan.tar.gz -C /opt/stylegan --strip-components=1 && rm /tmp/stylegan.tar.gz
WORKDIR /app
ARG TORCH_INDEX=cpu
COPY requirements-${TORCH_INDEX}.lock ./
RUN pip install --no-cache-dir --require-hashes --index-url https://download.pytorch.org/whl/${TORCH_INDEX} --extra-index-url https://pypi.org/simple -r requirements-${TORCH_INDEX}.lock

FROM runtime-deps AS api-base
COPY *.py ./
COPY templates ./templates
COPY assets ./checkfacedata/assets
COPY e4e ./e4e
COPY licenses ./licenses
COPY fixtures ./fixtures
COPY legacy-route-inventory.json ./
RUN mkdir -p /app/checkfacedata && chown -R 10001:10001 /app/checkfacedata
ENV PYTHONPATH=/opt/stylegan PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER 10001:10001
EXPOSE 8080
CMD ["python", "legacy_checkface.py"]

FROM api-base AS runtime-from-source
COPY --from=frontend-source /src/deploy-self-host /app/frontend

FROM api-base AS runtime
COPY --from=frontend-bundle /deploy-self-host /app/frontend
