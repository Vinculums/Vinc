ARG BASE_IMAGE=ubuntu:24.04
FROM ${BASE_IMAGE}
ARG RUST_VERSION
ARG AZURE_CLI_VERSION
COPY --chmod=0644 trust.pem /cloud-trust.pem
ENV DEBIAN_FRONTEND=noninteractive \
    SSL_CERT_FILE=/cloud-trust.pem \
    REQUESTS_CA_BUNDLE=/cloud-trust.pem \
    CURL_CA_BUNDLE=/cloud-trust.pem \
    NODE_EXTRA_CA_CERTS=/cloud-trust.pem \
    PATH=/opt/ci-venv/bin:/home/runner/.cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    RUNNER_TOOL_CACHE=/opt/hostedtoolcache \
    AGENT_TOOLSDIRECTORY=/opt/hostedtoolcache \
    CARGO_BUILD_JOBS=1
# The Cloud Docker daemon uses vfs: every filesystem layer is a full copy.
# Set WORKDIR before installing tools and keep installation in one RUN layer.
WORKDIR /opt/actions-runner
COPY runner.tar.gz node.tar.xz rustup-init entrypoint.py /opt/bootstrap/
RUN chmod 0644 /cloud-trust.pem \
    && printf 'Acquire::https::CaInfo "/cloud-trust.pem";\n' > /etc/apt/apt.conf.d/99cloud-ca \
    && if [ -n "${https_proxy:-}" ]; then \
         printf 'Acquire::https::Proxy "%s";\n' "$https_proxy" > /etc/apt/apt.conf.d/99cloud-proxy; \
       fi \
    && sed -i 's|http://|https://|g' /etc/apt/sources.list.d/ubuntu.sources \
    && apt-get update -qq \
    && apt-get install -y --no-install-recommends \
      ca-certificates curl git jq gh sudo unzip xz-utils docker.io \
      python3 python3-venv python-is-python3 build-essential pkg-config \
      libicu74 libssl3t64 libcurl4t64 liblttng-ust1t64 libunwind8 zlib1g \
      libwebkit2gtk-4.1-dev libappindicator3-dev librsvg2-dev patchelf libdbus-1-dev \
    && rm -rf /var/lib/apt/lists/* \
    && cp /cloud-trust.pem /etc/ssl/certs/ca-certificates.crt \
    && chmod 0755 /etc/ssl /etc/ssl/certs \
    && chmod 0644 /etc/ssl/certs/ca-certificates.crt \
    && useradd --create-home --uid 1001 --shell /bin/bash runner \
    && printf 'runner ALL=(ALL) NOPASSWD:ALL\n' > /etc/sudoers.d/runner \
    && chmod 0440 /etc/sudoers.d/runner \
    && mkdir -p /opt/hostedtoolcache /home/runner/probe \
    && chmod 0755 /opt /opt/bootstrap /opt/actions-runner /opt/hostedtoolcache \
    && tar -xJf /opt/bootstrap/node.tar.xz -C /usr/local --strip-components=1 \
    && python3 -m venv /opt/ci-venv \
    && /opt/ci-venv/bin/pip install --no-cache-dir ladybug==0.20.4 argon2-cffi pyyaml pytest pypdf \
    && python3 -m venv /opt/azure-cli \
    && /opt/azure-cli/bin/pip install --no-cache-dir "azure-cli==${AZURE_CLI_VERSION}" \
    && ln -s /opt/azure-cli/bin/az /usr/local/bin/az \
    && tar -xzf /opt/bootstrap/runner.tar.gz -C /opt/actions-runner \
    && chown -R runner:runner /opt/actions-runner /opt/hostedtoolcache /home/runner/probe \
    && chmod 0755 /opt/bootstrap/rustup-init \
    && runuser -u runner -- /opt/bootstrap/rustup-init -y --profile minimal --default-toolchain "${RUST_VERSION}" \
    && cp /opt/bootstrap/entrypoint.py /opt/entrypoint.py \
    && chmod 0644 /opt/entrypoint.py \
    && rm -rf /opt/bootstrap /root/.cache
USER runner
ENTRYPOINT ["python3", "/opt/entrypoint.py"]
