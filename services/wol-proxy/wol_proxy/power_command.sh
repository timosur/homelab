#!/bin/sh
# SSH forced command: never evaluate the client's command as shell code.
set -f
set -- ${SSH_ORIGINAL_COMMAND:-}
if [ "$#" -ne 7 ] || [ "$1" != sudo ] || [ "$2" != -n ] || \
   [ "$3" != /usr/local/sbin/dark-factory-power-guard ]; then
    exit 1
fi
case "$4" in
    fence|suspend) ;;
    *) exit 1 ;;
esac
exec /usr/bin/sudo -n /usr/local/sbin/dark-factory-power-guard "$4" "$5" "$6" "$7"
