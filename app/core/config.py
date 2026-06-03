import os
from pydantic_settings import BaseSettings
from pydantic import ConfigDict

class Settings(BaseSettings):
    PROJECT_NAME: str = "Monolithic Banking Management System"
    API_V1_STR: str = "/api"
    
    # Security
    SECRET_KEY: str = os.getenv("SECRET_KEY", "supersecretjwtkeyforbankingapplication123456!")
    REFRESH_SECRET_KEY: str = os.getenv("REFRESH_SECRET_KEY", "supersecretrefreshjwtkeyforbankingapplication123456!")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # Database
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", 
        "postgresql://postgres:postgres@localhost:5432/banking_db"
    )

    # File Storage (Azure Blob Storage)
    AZURE_STORAGE_CONNECTION_STRING: str = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
    AZURE_CONTAINER_NAME: str = os.getenv("AZURE_CONTAINER_NAME", "banking-documents")
    AZURE_VALIDATED_CONTAINER_NAME: str = os.getenv("AZURE_VALIDATED_CONTAINER_NAME", "process-and-validated")

    # Service Bus & Email Notifications
    AZURE_SERVICEBUS_CONNECTION_STRING: str = os.getenv("AZURE_SERVICEBUS_CONNECTION_STRING", "")
    SMTP_SERVER: str = os.getenv("SMTP_SERVER", "localhost")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "1025"))
    SMTP_USERNAME: str = os.getenv("SMTP_USERNAME", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    SMTP_SENDER: str = os.getenv("SMTP_SENDER", "no-reply@gokulbank.com")

    model_config = ConfigDict(case_sensitive=True)

settings = Settings()
