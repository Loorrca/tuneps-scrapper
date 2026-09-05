#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Installation de la veille TUNEPS sur une machine Linux.
#   ./install.sh            -> environnement virtuel + dépendances + tests
#   ./install.sh --timer        -> idem, puis le minuteur systemd (utilisateur)
#   ./install.sh --web-service  -> idem, puis l'interface web en permanence
#   ./install.sh --all          -> les deux
# Aucun droit root n'est nécessaire.
# ---------------------------------------------------------------------------
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv"
PY="$VENV/bin/python"

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m /!\\\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mErreur :\033[0m %s\n' "$*" >&2; exit 1; }

# --- 1. Python -------------------------------------------------------------
command -v python3 >/dev/null || die "python3 est introuvable. Installez-le puis relancez."
PYV=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
case "$PYV" in
  3.9|3.10|3.11|3.12|3.13|3.14) ;;
  *) warn "Python $PYV n'a pas été testé (3.9+ requis). On continue." ;;
esac
say "Python $PYV détecté."

# --- 2. Environnement virtuel ---------------------------------------------
if [ ! -d "$VENV" ]; then
  say "Création de l'environnement virtuel dans .venv"
  python3 -m venv "$VENV" 2>/dev/null || die \
"Impossible de créer l'environnement virtuel.
 Sur Debian/Ubuntu :  sudo apt install python3-venv
 Sur Fedora       :  sudo dnf install python3-virtualenv
 Sur Arch         :  déjà inclus dans le paquet python"
fi

say "Installation des dépendances"
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet -r "$ROOT/requirements.txt"

# --- 3. Fichiers de configuration -----------------------------------------
mkdir -p "$ROOT/data" "$ROOT/reports"
if [ ! -f "$ROOT/.env" ]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  chmod 600 "$ROOT/.env"
  warn "Fichier .env créé à partir du modèle — complétez-le avant le premier envoi."
fi

# --- 3b. Garde-fou Git -----------------------------------------------------
if [ -d "$ROOT/.git" ] && [ -f "$ROOT/scripts/pre-commit" ]; then
  hook="$ROOT/.git/hooks/pre-commit"
  if [ ! -e "$hook" ] || ! cmp -s "$ROOT/scripts/pre-commit" "$hook"; then
    cp "$ROOT/scripts/pre-commit" "$hook" && chmod +x "$hook"
    say "Garde-fou Git installé : .env et la base ne peuvent plus être commités."
  fi
fi

# --- 4. Tests --------------------------------------------------------------
say "Vérification (dictionnaire, corpus réel, bout-en-bout, TLS, interface web, échéances)"
cd "$ROOT"
"$PY" tests/test_matcher.py
"$PY" tests/test_corpus.py
"$PY" tests/test_pipeline.py
"$PY" tests/test_tls.py
"$PY" tests/test_web.py
"$PY" tests/test_deadlines.py

# --- 5. Minuteur systemd (optionnel) --------------------------------------
if [ "${1:-}" = "--timer" ] || [ "${1:-}" = "--all" ]; then
  UNITS="$HOME/.config/systemd/user"
  mkdir -p "$UNITS"

  cat > "$UNITS/tuneps-veille.service" <<EOF
[Unit]
Description=Veille des marches publics TUNEPS (drapeaux, banderoles, guirlandes)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$ROOT
ExecStart=$PY $ROOT/run.py run
# Le portail est parfois lent : on laisse du temps et on ne considère pas
# un echec reseau comme une panne du service.
TimeoutStartSec=900
SuccessExitStatus=0 2
EOF

  cat > "$UNITS/tuneps-veille.timer" <<EOF
[Unit]
Description=Declenche la veille TUNEPS trois fois par jour

[Timer]
OnCalendar=*-*-* 07,12,17:30:00
Persistent=true
RandomizedDelaySec=600

[Install]
WantedBy=timers.target
EOF

  systemctl --user daemon-reload
  systemctl --user enable --now tuneps-veille.timer
  say "Minuteur activé : 07h30, 12h30 et 17h30 chaque jour."
  echo
  systemctl --user list-timers tuneps-veille.timer --no-pager || true
  echo
  warn "Pour que la veille tourne même quand la session est fermée :"
  echo "     sudo loginctl enable-linger $USER"
fi

# --- 6. Service de l'interface web (optionnel) ----------------------------
if [ "${1:-}" = "--web-service" ] || [ "${1:-}" = "--all" ]; then
  UNITS="$HOME/.config/systemd/user"
  mkdir -p "$UNITS"

  cat > "$UNITS/tuneps-web.service" <<EOF
[Unit]
Description=Interface locale de suivi de la veille TUNEPS
After=default.target

[Service]
Type=simple
WorkingDirectory=$ROOT
# --no-browser : le service demarre avant la session graphique, ouvrir un
# navigateur depuis ici echouerait. La page reste disponible sur 127.0.0.1.
ExecStart=$PY $ROOT/run.py web --no-browser
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
EOF

  systemctl --user daemon-reload
  systemctl --user enable --now tuneps-web.service
  say "Interface disponible en permanence sur http://127.0.0.1:8765"
  systemctl --user --no-pager status tuneps-web.service | head -4 || true
  echo
  warn "Pour qu'elle survive a la fermeture de session :"
  echo "     sudo loginctl enable-linger $USER"
fi

cat <<EOF

------------------------------------------------------------------
Installation terminée.

  1. Renseignez vos identifiants d'envoi dans   .env
  2. Vérifiez le réseau et TLS                 ./veille doctor
  3. Vérifiez l'envoi                          ./veille test-email
  4. Première analyse (sans e-mail)            ./veille run --no-email
  5. Analyse normale                           ./veille run
  6. Interface de suivi                        ./veille web

Le premier « run » signale tous les avis correspondants du mois
courant et du mois précédent : c'est normal, la fois suivante
seules les nouveautés remonteront.
------------------------------------------------------------------
EOF
