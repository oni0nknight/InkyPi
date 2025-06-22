from plugins.base_plugin.base_plugin import BasePlugin
from PIL import Image
import logging
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import base64
import json
import io
import random

logger = logging.getLogger(__name__)


class GDrive(BasePlugin):
    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['api_key'] = {
            "required": True,
            "service": "Google Drive",
            "expected_key": "GDRIVE_ACCOUNT_INFOS"
        }
        return template_params

    def generate_image(self, settings, device_config):
        encoded_infos = device_config.load_env_key("GDRIVE_ACCOUNT_INFOS")
        if not encoded_infos:
            raise RuntimeError("Google Drive account infos not configured.")
        account_info = json.loads(base64.b64decode(encoded_infos).decode())

        folder_id = device_config.load_env_key("GDRIVE_FOLDER_ID")
        if not folder_id:
            raise RuntimeError("Missing folder id.")

        try:
            service = self.authenticate_gdrive(account_info)
        except Exception as e:
            raise RuntimeError(f"Impossible to connect to Google Drive: {e}")

        files = self.list_files_in_folder(service, folder_id)
        if not files:
            raise RuntimeError("No image in the provided Google Drive folder.")

        # Pick one image randomly
        chosen_file = random.choice(files)
        logger.info(f"Selected file: {chosen_file['name']} ({chosen_file['id']})")
        logger.info(f"Starting image download...")

        # Download image
        request = service.files().get_media(fileId=chosen_file['id'])
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status:
                percent = int(status.progress() * 100)
                logger.info(f"Download progress: {percent}%")
        fh.seek(0)  # Reset file pointer to start
        logger.info(f"Download complete...")

        # 4. Open image with PIL
        image = Image.open(fh)
        return image

    def authenticate_gdrive(self, service_account_info: dict):
        creds = service_account.Credentials.from_service_account_info(
            service_account_info,
            scopes=['https://www.googleapis.com/auth/drive.readonly'],
        )
        return build('drive', 'v3', credentials=creds)

    def list_files_in_folder(self, service, folder_id: str):
        query = f"'{folder_id}' in parents and trashed = false and mimeType contains 'image/'"
        results = service.files().list(q=query, fields="files(id, name)").execute()
        return results.get('files', [])
