# Agent image: Claude Code CLI + the toolchain an engineer/reviewer/QA agent needs to
# build and test a ticket inside an isolated container. One image serves every role;
# the role and model are chosen at `docker run` time by the conductor's dispatcher.
FROM node:22-slim

# git + gh for the repo work; curl/ca-certificates to fetch uv and the gh keyring.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
        curl \
        ca-certificates \
        gnupg \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | dd of=/etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLI (the agent runtime).
RUN npm install -g @anthropic-ai/claude-code

# Non-root user. /work is the repo mount; /home/agent/.claude is the state volume.
RUN useradd --create-home --shell /bin/bash agent \
    && mkdir -p /work \
    && chown -R agent:agent /work

USER agent
WORKDIR /work

# uv installs a real Python 3.12 (node:22-slim's system python is 3.11; the spec and the
# dem repo itself require 3.12). pytest is exposed globally as a baseline test runner;
# target repos still manage their own deps.
ENV PATH="/home/agent/.local/bin:${PATH}"
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && uv python install 3.12 \
    && ln -sf "$(uv python find 3.12)" /home/agent/.local/bin/python3.12 \
    && ln -sf python3.12 /home/agent/.local/bin/python3 \
    && ln -sf python3.12 /home/agent/.local/bin/python \
    && uv tool install --python 3.12 pytest

COPY --chown=agent:agent entrypoint.sh /opt/agent/entrypoint.sh
COPY --chown=agent:agent seed-claude.json /opt/agent/seed-claude.json
RUN chmod +x /opt/agent/entrypoint.sh

ENTRYPOINT ["/opt/agent/entrypoint.sh"]
