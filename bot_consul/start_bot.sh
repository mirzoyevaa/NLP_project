#!/usr/bin/env bash
# Один скрипт: venv → pip install → запуск бота
# ВАЖНО: Python только 3.10–3.13. На 3.14 падает сборка pydantic-core (PyO3 не знает 3.14).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
# Пакет bot_consul — из родителя storage_zone; travel_web_agent/search_* — из этой папки.
export PYTHONPATH="$(cd "$ROOT/.." && pwd):$ROOT"

_pick_python() {
  local try
  for try in python3.12 python3.13 python3.11 python3.10 python3; do
    if command -v "$try" &>/dev/null; then
      if "$try" -c "import sys; v=sys.version_info; raise SystemExit(0 if (3,10)<=(v.major,v.minor)<=(3,13) else 1)" 2>/dev/null; then
        echo "$try"
        return 0
      fi
    fi
  done
  return 1
}

if ! PYBIN="$(_pick_python)"; then
  echo ">>> ОШИБКА: нужен Python 3.10–3.13 (не 3.14)." >&2
  echo "    Сейчас у тебя, скорее всего, python3 → 3.14 — pydantic-core так не ставится." >&2
  echo "    Сделай, например:  pyenv install 3.12.8 && cd \"$ROOT\" && pyenv local 3.12.8" >&2
  echo "    Потом снова: ./start_bot.sh" >&2
  exit 1
fi

echo ">>> Использую интерпретатор: $PYBIN ($($PYBIN -c 'import sys; print(sys.version.split()[0])'))"

VENV_PY="$ROOT/.venv/bin/python3"

_need_recreate_venv() {
  [[ ! -x "$VENV_PY" ]] && return 0
  if ! "$VENV_PY" -c "import sys; v=sys.version_info; raise SystemExit(0 if (3,10)<=(v.major,v.minor)<=(3,13) else 1)" 2>/dev/null; then
    return 0
  fi
  return 1
}

if _need_recreate_venv; then
  echo ">>> Пересоздаю .venv через $PYBIN (старый venv не подходит или отсутствует)..."
  rm -rf "$ROOT/.venv"
  "$PYBIN" -m venv "$ROOT/.venv"
fi

VENV_PY="$ROOT/.venv/bin/python3"

echo ">>> Обновляю pip и ставлю зависимости из requirements.txt (может занять несколько минут)..."
"$VENV_PY" -m pip install -U pip wheel setuptools
"$VENV_PY" -m pip install -r "$ROOT/requirements.txt"

echo ">>> Проверка импорта aiogram..."
"$VENV_PY" -c "import aiogram; print('aiogram OK', aiogram.__version__)"

echo ">>> Останавливаю старый процесс бота (если был)..."
pkill -f "bot_consul.telegram_bot" 2>/dev/null || true
sleep 1

echo ">>> Запуск polling (ещё пишется в $ROOT/bot.log). Ctrl+C — стоп."
exec "$VENV_PY" -m bot_consul.telegram_bot
