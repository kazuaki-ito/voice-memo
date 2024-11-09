#!/usr/bin/env bash
set -o errexit

# 永続ディスク上のディレクトリ
PERSISTENT_DIR="/var/data"

# プロジェクト内のディレクトリ
SQLITE_DIR="/app/db"
RECORDINGS_DIR="/app/recordings"

# 永続ディスク上にディレクトリが存在しない場合は作成
mkdir -p "$PERSISTENT_DIR/db"
mkdir -p "$PERSISTENT_DIR/recordings"

# プロジェクト内のディレクトリが存在する場合は削除
rm -rf "$SQLITE_DIR"
rm -rf "$RECORDINGS_DIR"

# シンボリックリンクの作成
ln -s "$PERSISTENT_DIR/db" "$SQLITE_DIR"
ln -s "$PERSISTENT_DIR/recordings" "$RECORDINGS_DIR"
