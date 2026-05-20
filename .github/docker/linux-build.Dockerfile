ARG UE_IMAGE
FROM ${UE_IMAGE}

USER root

ARG SCCACHE_VERSION=0.10.0
ARG ISPC_VERSION=v1.30.0

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      flatbuffers-compiler \
      zstd \
 && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL "https://github.com/mozilla/sccache/releases/download/v${SCCACHE_VERSION}/sccache-v${SCCACHE_VERSION}-x86_64-unknown-linux-musl.tar.gz" -o /tmp/sccache.tar.gz \
 && tar -xzf /tmp/sccache.tar.gz -C /tmp \
 && install "/tmp/sccache-v${SCCACHE_VERSION}-x86_64-unknown-linux-musl/sccache" /usr/local/bin/sccache \
 && rm -rf /tmp/sccache*

RUN curl -fsSL "https://github.com/ispc/ispc/releases/download/${ISPC_VERSION}/ispc-${ISPC_VERSION}-linux.tar.gz" -o /tmp/ispc.tar.gz \
 && tar -xzf /tmp/ispc.tar.gz -C /tmp \
 && mkdir -p /home/ue4/UnrealEngine/Engine/Source/ThirdParty/Intel/ISPC/bin/Linux \
 && install "/tmp/ispc-${ISPC_VERSION}-linux/bin/ispc" /home/ue4/UnrealEngine/Engine/Source/ThirdParty/Intel/ISPC/bin/Linux/ispc \
 && rm -rf /tmp/ispc*