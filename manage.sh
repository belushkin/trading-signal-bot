#!/bin/sh
set -eu

usage() {
    cat <<'EOF'
Usage: ./manage.sh COMMAND [OPTIONS]

  start                 Build images and start the application
  stop                  Stop services without deleting containers or data
  restart               Restart services using their existing configuration
  status                Show all service containers and their status
  logs [SERVICE]        Follow the latest logs (app, api, dashboard, or db)
  health                Check the collector heartbeat and database connection
  report [OPTIONS]      Show signal evaluation statistics as JSON
  config                Validate the Compose configuration without printing secrets
  help                  Show this help

Examples:
  ./manage.sh logs app
  ./manage.sh report --since-days 90 --horizon-minutes 60

Configure .env before starting. The dashboard defaults to http://127.0.0.1:8080.
EOF
}

fail_usage() {
    printf '%s\n' "$1" >&2
    usage >&2
    exit 2
}

command=${1:-help}
if [ "$#" -gt 0 ]; then
    shift
fi

case "$command" in
    help|-h|--help)
        [ "$#" -eq 0 ] || fail_usage "help does not accept arguments."
        usage
        exit 0
        ;;
    start|stop|restart|status|health|config)
        [ "$#" -eq 0 ] || fail_usage "$command does not accept arguments."
        ;;
    logs)
        [ "$#" -le 1 ] || fail_usage "logs accepts at most one service."
        if [ "$#" -eq 1 ]; then
            case "$1" in
                app|api|dashboard|db) ;;
                *) fail_usage "Unknown service: $1" ;;
            esac
        fi
        ;;
    report) ;;
    *) fail_usage "Unknown command: $command" ;;
esac

cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

case "$command" in
    start) exec docker compose up -d --build ;;
    stop) exec docker compose stop ;;
    restart) exec docker compose restart ;;
    status) exec docker compose ps --all ;;
    logs) exec docker compose logs --tail=100 --follow "$@" ;;
    health) exec docker compose exec -T app python -m stellar_intelligence_bot health ;;
    report) exec docker compose exec -T app python -m stellar_intelligence_bot report "$@" ;;
    config) exec docker compose config --quiet ;;
esac
