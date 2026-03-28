/// Shell preamble that augments PATH for ansible/pip locations.
/// Uses `find` instead of shell globs to be safe in both bash and zsh
/// (zsh errors on unmatched globs by default).
///
/// On macOS, also creates a Docker config without `credsStore` and
/// `currentContext` so that `docker compose up` doesn't try to access
/// the macOS Keychain (unavailable in non-interactive SSH sessions) or
/// reference a Docker Desktop context that may not exist (e.g., when
/// using Colima instead).
pub const SHELL_PATH_PREAMBLE: &str = "\
    for d in \"$HOME/.busibox/venv/bin\" \"$HOME/.local/bin\" /usr/local/bin /opt/homebrew/bin; do [ -d \"$d\" ] && export PATH=\"$d:$PATH\"; done; \
    for d in $(find \"$HOME/Library/Python\" -maxdepth 2 -name bin -type d 2>/dev/null); do export PATH=\"$d:$PATH\"; done; \
    if [ \"$(uname -s)\" = \"Darwin\" ]; then \
        if [ -f \"$HOME/.docker/config.json\" ] && grep -qE 'credsStore|currentContext' \"$HOME/.docker/config.json\" 2>/dev/null; then \
            _busibox_docker_cfg=$(mktemp -d); \
            python3 -c \"import json,sys; c=json.load(open(sys.argv[1])); c.pop('credsStore',None); c.pop('currentContext',None); json.dump(c,open(sys.argv[2],'w'),indent=2)\" \
                \"$HOME/.docker/config.json\" \"$_busibox_docker_cfg/config.json\"; \
            for _sd in cli-plugins contexts; do [ -d \"$HOME/.docker/$_sd\" ] && ln -s \"$HOME/.docker/$_sd\" \"$_busibox_docker_cfg/$_sd\"; done; \
            export DOCKER_CONFIG=\"$_busibox_docker_cfg\"; \
        fi; \
        if [ -z \"$DOCKER_HOST\" ] && ! docker info &>/dev/null 2>&1; then \
            for _sock in \"$HOME/.colima/default/docker.sock\" \"$HOME/.colima/docker.sock\"; do \
                [ -S \"$_sock\" ] && export DOCKER_HOST=\"unix://$_sock\" && break; \
            done; \
        fi; \
    fi; ";
