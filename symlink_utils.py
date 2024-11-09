import os

def create_symlinks():
    # Render.com上で実行されているか確認
    if os.getenv('RENDER'):
        # 永続ストレージのパス
        persistent_db_path = '/var/data/db'
        persistent_recordings_path = '/var/data/recordings'

        # アプリケーション内のシンボリックリンクのパス
        app_db_path = '/app/db'
        app_recordings_path = '/app/recordings'

        # 永続ストレージ上のディレクトリが存在しない場合は作成
        os.makedirs(persistent_db_path, exist_ok=True)
        os.makedirs(persistent_recordings_path, exist_ok=True)

        # 既存のシンボリックリンクを削除
        if os.path.islink(app_db_path):
            os.unlink(app_db_path)
        if os.path.islink(app_recordings_path):
            os.unlink(app_recordings_path)

        # シンボリックリンクを作成
        os.symlink(persistent_db_path, app_db_path)
        os.symlink(persistent_recordings_path, app_recordings_path)
    else:
        print("Render.com環境外のため、シンボリックリンクの作成をスキップします。")
