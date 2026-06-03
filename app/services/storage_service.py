import os
import uuid
import logging
from azure.storage.blob import BlobServiceClient, ContentSettings
from app.core.config import settings

logger = logging.getLogger(__name__)

class StorageService:
    def __init__(self):
        self.conn_str = settings.AZURE_STORAGE_CONNECTION_STRING
        self.container_name = settings.AZURE_CONTAINER_NAME
        self.client = None
        
        if self.conn_str and self.conn_str.strip():
            try:
                self.client = BlobServiceClient.from_connection_string(self.conn_str)
                container_client = self.client.get_container_client(self.container_name)
                try:
                    container_client.create_container()
                except Exception:
                    pass
                logger.info("Azure Blob Storage initialized successfully.")
            except Exception as e:
                logger.error(f"Failed to initialize Azure Blob Storage: {e}")

    def upload_file(self, file_content: bytes, filename: str, content_type: str, metadata: dict = None) -> dict:
        ext = os.path.splitext(filename)[1].lower()
        unique_filename = f"{uuid.uuid4()}{ext}"
        
        if self.client:
            try:
                blob_client = self.client.get_blob_client(container=self.container_name, blob=unique_filename)
                content_settings = ContentSettings(content_type=content_type)
                blob_client.upload_blob(file_content, overwrite=True, content_settings=content_settings, metadata=metadata)
                return {"url": blob_client.url, "blob_name": unique_filename}
            except Exception as e:
                logger.error(f"Azure upload failed, falling back to local storage: {e}")
                return self._local_fallback_upload(file_content, unique_filename)
        else:
            return self._local_fallback_upload(file_content, unique_filename)

    def _local_fallback_upload(self, file_content: bytes, unique_filename: str) -> dict:
        os.makedirs("static/uploads", exist_ok=True)
        local_path = os.path.join("static/uploads", unique_filename)
        with open(local_path, "wb") as f:
            f.write(file_content)
        # Note: in a monolithic app, static files are mounted at /static, so /static/uploads/filename
        url = f"/static/uploads/{unique_filename}"
        return {"url": url, "blob_name": unique_filename}

    def delete_file(self, blob_name: str) -> bool:
        if self.client:
            try:
                blob_client = self.client.get_blob_client(container=self.container_name, blob=blob_name)
                blob_client.delete_blob()
                return True
            except Exception as e:
                logger.error(f"Azure delete failed: {e}")
                return False
        else:
            local_path = os.path.join("static/uploads", blob_name)
            if os.path.exists(local_path):
                try:
                    os.remove(local_path)
                    return True
                except Exception:
                    return False
            return False

storage_service = StorageService()
