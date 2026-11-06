#!/usr/bin/env bash
# Removes everything this project created on the instance: the containers, the 3 volumes (database, archive,
# restore drill), the network and the demo image.   Usage: ./scripts/destroy.sh [--keep-env]
set -uo pipefail
. "$(dirname "$0")/lib.sh"

log "Stopping the stack and deleting its volumes (this deletes the database AND the backups)"
$DC --profile restore down -v --remove-orphans

leftover="$($DOCKER ps -aq --filter 'name=^bdr-')"
[ -z "$leftover" ] || $DOCKER rm -f $leftover >/dev/null

log "Removing the demo image"
$DOCKER rmi -f backupdr-demo:local >/dev/null 2>&1 || true
$DOCKER image prune -f >/dev/null 2>&1 || true

rm -f "$ROOT"/results/*.json "$ROOT"/results/*.tmp
if [ "${1:-}" != "--keep-env" ]; then rm -f "$ROOT/.env"; fi

echo
echo "Cleanup done. (The postgres and python images stay cached; remove them with: sudo docker image prune -af)"
echo "IMPORTANT: terminate the EC2 instance in the AWS console (EC2 > Instances >"
echo "Instance state > Terminate) so it stops costing money. Then delete the security group."
