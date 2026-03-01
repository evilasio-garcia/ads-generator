# -*- coding: utf-8 -*-
import os
import re
import json
import argparse
from datetime import datetime
from typing import Any, Dict, List, Optional
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Database Setup (sync with app.py)
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://app_ads_generator_usr:app_ads_generator_psw@localhost:5432/app_ads_generator_db",
)

Base = declarative_base()

class UserConfig(Base):
    __tablename__ = "user_config"
    id = Column(Integer, primary_key=True)
    user_id = Column(String)
    data = Column(JSONB)
    updated_at = Column(DateTime)

def get_drive_config():
    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        # Assuming we take the first config found or a specific user_id if needed
        # For simplicity in this standalone script, we'll take the most recently updated one
        config = session.query(UserConfig).order_by(UserConfig.updated_at.desc()).first()
        if not config:
            return None
        return config.data.get("google_drive", {})
    finally:
        session.close()

def build_drive_service(credentials_json):
    try:
        creds_dict = json.loads(credentials_json)
        creds = service_account.Credentials.from_service_account_info(
            creds_dict, scopes=["https://www.googleapis.com/auth/drive"]
        )
        return build("drive", "v3", credentials=creds)
    except Exception as e:
        print(f"Erro ao construir serviço Drive: {e}")
        return None

def is_sku_pattern(filename, sku):
    """
    Check if filename starts with the SKU radical.
    Everything starting with the SKU should remain in the root.
    """
    return filename.lower().startswith(sku.lower())

def is_image(filename, mime_type):
    image_extensions = ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff', '.avif', '.svg')
    return mime_type.startswith("image/") or filename.lower().endswith(image_extensions)

def is_video(filename, mime_type):
    video_extensions = ('.mp4', '.mov', '.avi', '.mkv', '.wmv', '.flv', '.webm', '.m4v')
    return mime_type.startswith("video/") or filename.lower().endswith(video_extensions) or mime_type == "application/vnd.google-apps.video"

def _escape_q(s: str) -> str:
    """Escapa aspas simples para queries do Google Drive."""
    return s.replace("'", "\\'")

def _find_file_in_folder(service, folder_id: str, filename: str, mime_type: str = None) -> Optional[str]:
    """Find a file or folder by name in a folder. Returns ID or None."""
    safe_filename = _escape_q(filename)
    query = f"name='{safe_filename}' and '{folder_id}' in parents and trashed=false"
    if mime_type:
        query += f" and mimeType='{mime_type}'"
    
    results = service.files().list(
        q=query, 
        fields="files(id)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()
    files = results.get("files", [])
    return files[0]["id"] if files else None

def get_or_create_subfolder(service, parent_id, name, dry_run=False):
    query = f"name='{name}' and '{parent_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    results = service.files().list(q=query, fields="files(id, name)", supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
    folders = results.get("files", [])
    if folders:
        return folders[0]["id"]
    
    if dry_run:
        print(f"  [DRY-RUN] Criaria pasta '{name}' em {parent_id}")
        return "DRY_RUN_ID"
        
    body = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id]
    }
    folder = service.files().create(body=body, fields="id", supportsAllDrives=True).execute()
    return folder["id"]

def main():
    parser = argparse.ArgumentParser(description="Reorganiza arquivos do Google Drive por SKU.")
    parser.add_argument("--dry-run", action="store_true", help="Apenas simula as ações sem mover arquivos.")
    args = parser.parse_args()

    print(f"--- Iniciando Reorganização do Drive ({'SIMULÇÃO' if args.dry_run else 'EXECUÇÃO'}) ---")
    
    drive_cfg = get_drive_config()
    if not drive_cfg:
        print("Erro: Nenhuma configuração de Drive encontrada no banco de dados.")
        return

    root_folder_id = drive_cfg.get("folder_id")
    credentials_json = drive_cfg.get("credentials_json")

    if not root_folder_id or not credentials_json:
        print("Erro: Configuração de Drive incompleta.")
        return

    service = build_drive_service(credentials_json)
    if not service:
        return

    # Listar subpastas da raiz (pastas de SKU) com suporte a paginação
    query = f"'{root_folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    sku_folders = []
    page_token = None
    
    while True:
        results = service.files().list(
            q=query, 
            fields="nextPageToken, files(id, name)", 
            supportsAllDrives=True, 
            includeItemsFromAllDrives=True,
            pageSize=1000,
            pageToken=page_token
        ).execute()
        
        sku_folders.extend(results.get("files", []))
        page_token = results.get("nextPageToken")
        if not page_token:
            break

    total_folders_processed = 0
    folders_with_action = 0

    for folder in sku_folders:
        sku = folder["name"].strip()
        folder_id = folder["id"]
        
        # 1. Verificar se existe a pasta legada "RAW" (tentando variações de nome)
        raw_legacy_id = None
        for name_variation in ["RAW", "raw", "Raw"]:
            raw_legacy_id = _find_file_in_folder(service, folder_id, name_variation, "application/vnd.google-apps.folder")
            if raw_legacy_id:
                break
        
        # 2. Coletar arquivos da raiz e da pasta RAW
        all_candidate_files = [] # List of (f_id, f_name, f_mime, current_parent_id)
        
        # Buscar da raiz
        res_root = service.files().list(q=f"'{folder_id}' in parents and trashed=false", fields="files(id, name, mimeType)", supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
        for f in res_root.get("files", []):
            if f["mimeType"] != "application/vnd.google-apps.folder":
                all_candidate_files.append((f["id"], f["name"].strip(), f["mimeType"], folder_id))
        
        # Buscar da pasta RAW legada (se existir)
        if raw_legacy_id:
            res_raw = service.files().list(q=f"'{raw_legacy_id}' in parents and trashed=false", fields="files(id, name, mimeType)", supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
            for f in res_raw.get("files", []):
                if f["mimeType"] != "application/vnd.google-apps.folder":
                    all_candidate_files.append((f["id"], f["name"].strip(), f["mimeType"], raw_legacy_id))

        moves = [] # Lista de (file_id, file_name, target_folder_id_or_none, target_name_for_log)
        
        target_subfolders = {} # Cache de IDs: {"RAW_IMG": id, ...}

        for f_id, f_name, f_mime, current_parent_id in all_candidate_files:
            # Regra ajustada:
            # 1. Se o arquivo está na RAIZ (folder_id) E segue o padrão do SKU, MANTÉM na RAIZ.
            # 2. Se o arquivo está na pasta legada "RAW", SEMPRE será reclassificado (independente de padrão).
            if current_parent_id == folder_id and is_sku_pattern(f_name, sku):
                continue
            
            # Se não é o caso acima, decide a subpasta (reclassificação)
            target_name = "RAW_KDB"
            if is_image(f_name, f_mime):
                target_name = "RAW_IMG"
            elif is_video(f_name, f_mime):
                target_name = "RAW_MOV"
            
            # Verificar ID da pasta alvo
            if target_name not in target_subfolders:
                target_subfolders[target_name] = get_or_create_subfolder(service, folder_id, target_name, args.dry_run)
            
            target_id = target_subfolders[target_name]
            if current_parent_id != target_id:
                moves.append((f_id, f_name, target_id, target_name))

        if moves:
            folders_with_action += 1
            print(f"Pasta: {sku} ({len(moves)} arquivos para organizar)")
            
            for f_id, f_name, target_id, target_name in moves:
                if args.dry_run:
                    print(f"  [DRY-RUN] Moveria {f_name} -> {target_name}")
                else:
                    try:
                        file = service.files().get(fileId=f_id, fields="parents", supportsAllDrives=True).execute()
                        previous_parents = ",".join(file.get("parents"))
                        service.files().update(
                            fileId=f_id,
                            addParents=target_id,
                            removeParents=previous_parents,
                            fields="id, parents",
                            supportsAllDrives=True
                        ).execute()
                        print(f"  Movido: {f_name} -> {target_name}")
                    except Exception as e:
                        print(f"  Erro ao mover {f_name}: {e}")

        # 3. Se a pasta RAW legada existir, verificar se ficou vazia e deletar
        if raw_legacy_id and not args.dry_run:
            try:
                # Verifica se está vazia
                check_empty = service.files().list(
                    q=f"'{raw_legacy_id}' in parents and trashed=false", 
                    fields="files(id)", 
                    supportsAllDrives=True, 
                    includeItemsFromAllDrives=True,
                    pageSize=1
                ).execute()
                
                if not check_empty.get("files"):
                    # Tenta mover para lixeira (update trashed=True) que é mais permissivo que delete()
                    service.files().update(fileId=raw_legacy_id, body={'trashed': True}, supportsAllDrives=True).execute()
                    print(f"  Pasta legada 'RAW' enviada para a lixeira.")
            except Exception as e:
                # Se falhar aqui, provavelmente é restrição do Drive Compartilhado (Content Manager não apaga pastas)
                print(f"  Aviso: Pasta 'RAW' ({raw_legacy_id}) está vazia mas não pôde ser removida.")
                print(f"  Dica: Para o script apagar pastas, a conta de serviço precisa ser 'Administrador' (Manager) em vez de 'Administrador de Conteúdo'.")

        total_folders_processed += 1

    print(f"\nReorganização concluída.")
    print(f"Total de pastas processadas: {total_folders_processed}")
    print(f"Total de pastas com ações: {folders_with_action}")

if __name__ == "__main__":
    main()
