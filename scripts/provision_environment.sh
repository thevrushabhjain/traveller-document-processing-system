#!/bin/bash
# Re-provisions system-level dependencies for this preview sandbox.
#
# Everything under /app persists across a container restart, but system
# packages installed with apt (PostgreSQL, Poppler) and their data
# directories under /usr and /var live outside /app and are wiped on
# restart. If the preview environment ever comes back up with the backend
# falling back to SQLite and PDF uploads failing, re-run this script.
#
# This has no effect on real deployments (Docker/Railway/Render), which
# use persistent volumes / managed Postgres and never need this script.
set -e

echo "[1/3] Installing system packages (PostgreSQL, Poppler, OpenCV libs)..."
apt-get update -qq
apt-get install -y -qq postgresql postgresql-contrib poppler-utils \
    libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 libgomp1

echo "[2/3] Starting PostgreSQL and creating the traveldocs database..."
service postgresql start
sleep 2
su - postgres -c "psql -c \"CREATE USER traveldocs WITH PASSWORD 'traveldocs';\"" || true
su - postgres -c "psql -c \"CREATE DATABASE traveldocs OWNER traveldocs;\"" || true

echo "[3/3] Registering PostgreSQL with supervisor..."
cat > /etc/supervisor/conf.d/postgresql.conf << 'EOF'
[program:postgresql]
command=/usr/lib/postgresql/15/bin/postgres -D /var/lib/postgresql/15/main -c config_file=/etc/postgresql/15/main/postgresql.conf
user=postgres
autostart=true
autorestart=true
stdout_logfile=/var/log/supervisor/postgresql.out.log
stderr_logfile=/var/log/supervisor/postgresql.err.log
priority=1
EOF
service postgresql stop || true
supervisorctl reread
supervisorctl update
supervisorctl restart backend

echo "Done. Check: supervisorctl status"
