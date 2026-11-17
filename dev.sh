#!/bin/sh
# Common commands. Usage: ./dev.sh <command>
#   up | down | logs | migrate | seed | seed-demo | admin <phone> "<name>" | test | lint
set -e
cd "$(dirname "$0")"
case "$1" in
  up)        docker compose up --build -d ;;
  down)      docker compose down ;;
  logs)      docker compose logs -f api worker ;;
  migrate)   docker compose exec api alembic upgrade head ;;
  seed)      for c in seed-base seed-company seed-catalog; do docker compose exec api python -m app.cli $c; done ;;
  seed-demo) docker compose exec api python -m app.cli seed-demo ;;
  admin)     docker compose exec -it api python -m app.cli create-admin --phone "$2" --name "$3" ;;
  test)      docker compose exec -e ERP_TEST_DATABASE_URL=postgresql+psycopg://erp:erp@postgres:5432/tayal_erp_test api pytest ;;
  lint)      docker compose exec api ruff check app tests ;;
  *)         sed -n '2,3p' "$0"; exit 1 ;;
esac
