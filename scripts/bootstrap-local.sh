#!/usr/bin/env bash
# Create the /opt/tljh layout on a machine that has a plain JupyterHub rather
# than a real TLJH install, so that local and production paths are identical.
#
# The one thing a local box needs on top of the TLJH layout is a bridge for
# kernel discovery: a stock JupyterHub searches /usr/local/share/jupyter, but
# not /opt/tljh/user/share/jupyter. On real TLJH no bridge is needed, because
# the single-user server's sys.prefix already is /opt/tljh/user.
#
# Usage: sudo scripts/bootstrap-local.sh

set -euo pipefail

TLJH_ROOT=${TLJH_ROOT:-/opt/tljh}
USER_PREFIX="${TLJH_ROOT}/user"
JUPYTER_SHARE="${USER_PREFIX}/share/jupyter"
SYSTEM_SHARE=${SYSTEM_SHARE:-/usr/local/share/jupyter}

umask 022

echo "creating ${TLJH_ROOT} layout"
if ! mkdir -p \
    "${USER_PREFIX}/envs" \
    "${JUPYTER_SHARE}/kernels" \
    "${TLJH_ROOT}/config" \
    "${TLJH_ROOT}/environ/repo" \
    "${TLJH_ROOT}/environ/state" \
    "${TLJH_ROOT}/environ/logs" 2>/dev/null; then
    echo "cannot write under ${TLJH_ROOT}; re-run with sudo" >&2
    echo "(or set TLJH_ROOT and SYSTEM_SHARE to somewhere writable, for a dry run)" >&2
    exit 1
fi

chmod -R a+rX "${TLJH_ROOT}"

if [[ -e ${SYSTEM_SHARE} && ! -L ${SYSTEM_SHARE} ]]; then
    echo "warning: ${SYSTEM_SHARE} exists and is not a symlink; leaving it alone." >&2
    echo "         Point JUPYTER_PATH at ${JUPYTER_SHARE} instead, for example by adding" >&2
    echo "         c.Spawner.environment = {'JUPYTER_PATH': '${JUPYTER_SHARE}'}" >&2
    echo "         to /etc/jupyterhub/jupyterhub_config.py and restarting the hub." >&2
elif mkdir -p "$(dirname "${SYSTEM_SHARE}")" 2>/dev/null &&
    ln -sfn "${JUPYTER_SHARE}" "${SYSTEM_SHARE}" 2>/dev/null; then
    echo "linked ${SYSTEM_SHARE} -> ${JUPYTER_SHARE}"
else
    echo "warning: cannot create ${SYSTEM_SHARE}; re-run with sudo to make kernels" >&2
    echo "         visible to JupyterHub, or set JUPYTER_PATH=${JUPYTER_SHARE}" >&2
fi

if [[ ! -f ${TLJH_ROOT}/config/environ.toml ]]; then
    echo
    echo "next: install a config, for example"
    echo "  sudo cp deploy/config.local.toml ${TLJH_ROOT}/config/environ.toml"
fi

echo
echo "layout ready:"
find "${TLJH_ROOT}" -maxdepth 3 -type d | sort | sed 's/^/  /'
