"""
Authentication Module

Provides JWT-based authentication with email/password and Google Sign-In support.
"""

import os
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, EmailStr
from jose import JWTError, jwt
import bcrypt

# Google Auth
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

logger = logging.getLogger(__name__)

# =============================================================================
# Configuration
# =============================================================================

# JWT Settings - Use environment variable in production
SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "dev-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

# Google OAuth - Client ID from Google Cloud Console
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")

# Bearer token security scheme
security = HTTPBearer()


# =============================================================================
# Models
# =============================================================================

class UserAuth(BaseModel):
    """User authentication record stored in Firestore."""
    user_id: str
    email: str
    password_hash: Optional[str] = None  # None for Google-only users
    google_id: Optional[str] = None  # Google sub claim
    is_onboarded: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_firestore_dict(self) -> dict:
        """Convert to Firestore-compatible dictionary."""
        return {
            "user_id": self.user_id,
            "email": self.email,
            "password_hash": self.password_hash,
            "google_id": self.google_id,
            "is_onboarded": self.is_onboarded,
            "created_at": self.created_at.isoformat(),
        }
    
    @classmethod
    def from_firestore_dict(cls, data: dict) -> "UserAuth":
        """Create from Firestore document."""
        created_at = data.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        elif created_at is None:
            created_at = datetime.now(timezone.utc)
            
        return cls(
            user_id=data["user_id"],
            email=data["email"],
            password_hash=data.get("password_hash"),
            google_id=data.get("google_id"),
            is_onboarded=data.get("is_onboarded", False),
            created_at=created_at,
        )


class Token(BaseModel):
    """JWT token response."""
    access_token: str
    token_type: str = "bearer"
    user_id: str
    email: str
    is_onboarded: bool


class SignupRequest(BaseModel):
    """Email/password signup request."""
    email: EmailStr
    password: str = Field(..., min_length=6)


class LoginRequest(BaseModel):
    """Email/password login request."""
    email: EmailStr
    password: str


class GoogleAuthRequest(BaseModel):
    """Google Sign-In request with ID token."""
    credential: str  # The ID token from Google Identity Services


class UserResponse(BaseModel):
    """User info response."""
    user_id: str
    email: str
    is_onboarded: bool


# =============================================================================
# Password Utilities
# =============================================================================

def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    password_bytes = password.encode('utf-8')
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password_bytes, salt)
    return hashed.decode('utf-8')


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    password_bytes = plain_password.encode('utf-8')
    hashed_bytes = hashed_password.encode('utf-8')
    return bcrypt.checkpw(password_bytes, hashed_bytes)


# =============================================================================
# JWT Utilities
# =============================================================================

def create_access_token(user_id: str, email: str, is_onboarded: bool) -> str:
    """Create a JWT access token."""
    expire = datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    
    payload = {
        "sub": user_id,
        "email": email,
        "is_onboarded": is_onboarded,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and validate a JWT token."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError as e:
        logger.warning(f"JWT decode error: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


# =============================================================================
# Google OAuth Utilities
# =============================================================================

def verify_google_token(credential: str) -> dict:
    """
    Verify a Google ID token and return the user info.
    
    Returns dict with: sub (Google user ID), email, name, picture
    """
    if not GOOGLE_CLIENT_ID:
        # Mock behavior if client ID not set but we're in a mock flow?
        # Ideally, we should not be calling this in mock flow if we mock the auth service.
        # But for robustness:
        pass
        
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Google OAuth not configured (missing GOOGLE_CLIENT_ID)"
        )
    
    try:
        idinfo = id_token.verify_oauth2_token(
            credential,
            google_requests.Request(),
            GOOGLE_CLIENT_ID
        )
        
        # Verify issuer
        if idinfo['iss'] not in ['accounts.google.com', 'https://accounts.google.com']:
            raise ValueError('Invalid issuer')
        
        return {
            "google_id": idinfo['sub'],
            "email": idinfo['email'],
            "name": idinfo.get('name', ''),
            "picture": idinfo.get('picture', ''),
        }
        
    except ValueError as e:
        logger.warning(f"Google token verification failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google token"
        )


# =============================================================================
# Dependency Injection
# =============================================================================

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    """
    FastAPI dependency to get the current authenticated user from JWT.
    
    Returns dict with user_id, email, is_onboarded from token claims.
    """
    token = credentials.credentials
    payload = decode_token(token)
    
    return {
        "user_id": payload["sub"],
        "email": payload["email"],
        "is_onboarded": payload.get("is_onboarded", False),
    }


# =============================================================================
# Auth Service
# =============================================================================

class AuthService:
    """
    Authentication service for managing users in Firestore.
    """
    
    def __init__(self, db, collection_name: str = "auth_users"):
        """
        Initialize the auth service.
        
        Args:
            db: Firestore client instance
            collection_name: Collection name for auth users
        """
        self.db = db
        self.collection = db.collection(collection_name)
    
    def get_user_by_email(self, email: str) -> Optional[UserAuth]:
        """Get user by email address."""
        docs = self.collection.where("email", "==", email).limit(1).stream()
        
        for doc in docs:
            return UserAuth.from_firestore_dict(doc.to_dict())
        
        return None
    
    def get_user_by_google_id(self, google_id: str) -> Optional[UserAuth]:
        """Get user by Google ID."""
        docs = self.collection.where("google_id", "==", google_id).limit(1).stream()
        
        for doc in docs:
            return UserAuth.from_firestore_dict(doc.to_dict())
        
        return None
    
    def get_user_by_id(self, user_id: str) -> Optional[UserAuth]:
        """Get user by user ID."""
        doc = self.collection.document(user_id).get()
        
        if doc.exists:
            return UserAuth.from_firestore_dict(doc.to_dict())
        
        return None
    
    def create_user(self, user: UserAuth) -> UserAuth:
        """Create a new user."""
        self.collection.document(user.user_id).set(user.to_firestore_dict())
        return user
    
    def update_user(self, user: UserAuth) -> UserAuth:
        """Update an existing user."""
        self.collection.document(user.user_id).update(user.to_firestore_dict())
        return user
    
    def set_onboarded(self, user_id: str) -> None:
        """Mark a user as onboarded."""
        self.collection.document(user_id).update({"is_onboarded": True})
    
    def signup_with_email(self, email: str, password: str) -> Token:
        """
        Create a new user with email/password.
        
        Raises HTTPException if email already exists.
        """
        # Check if user exists
        existing = self.get_user_by_email(email)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered"
            )
        
        # Create user
        import uuid
        user_id = f"user_{uuid.uuid4().hex[:12]}"
        
        user = UserAuth(
            user_id=user_id,
            email=email,
            password_hash=hash_password(password),
            is_onboarded=False,
        )
        
        self.create_user(user)
        
        # Generate token
        access_token = create_access_token(user.user_id, user.email, user.is_onboarded)
        
        return Token(
            access_token=access_token,
            user_id=user.user_id,
            email=user.email,
            is_onboarded=user.is_onboarded,
        )
    
    def login_with_email(self, email: str, password: str) -> Token:
        """
        Authenticate with email/password.
        
        Raises HTTPException if credentials are invalid.
        """
        user = self.get_user_by_email(email)
        
        if not user or not user.password_hash:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password"
            )
        
        if not verify_password(password, user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password"
            )
        
        # Generate token
        access_token = create_access_token(user.user_id, user.email, user.is_onboarded)
        
        return Token(
            access_token=access_token,
            user_id=user.user_id,
            email=user.email,
            is_onboarded=user.is_onboarded,
        )
    
    def auth_with_google(self, credential: str) -> Token:
        """
        Authenticate with Google ID token.
        
        Creates a new user if this is their first login.
        """
        # Verify Google token
        google_info = verify_google_token(credential)
        
        # Check if user exists by Google ID
        user = self.get_user_by_google_id(google_info["google_id"])
        
        if not user:
            # Check if email exists (link accounts)
            user = self.get_user_by_email(google_info["email"])
            
            if user:
                # Link Google account to existing user
                user.google_id = google_info["google_id"]
                self.update_user(user)
            else:
                # Create new user
                import uuid
                user_id = f"user_{uuid.uuid4().hex[:12]}"
                
                user = UserAuth(
                    user_id=user_id,
                    email=google_info["email"],
                    google_id=google_info["google_id"],
                    is_onboarded=False,
                )
                
                self.create_user(user)
        
        # Generate token
        access_token = create_access_token(user.user_id, user.email, user.is_onboarded)
        
        return Token(
            access_token=access_token,
            user_id=user.user_id,
            email=user.email,
            is_onboarded=user.is_onboarded,
        )

# =============================================================================
# Mock Auth Service
# =============================================================================

class MockAuthService:
    """
    In-memory mock authentication service for local testing.
    """
    
    def __init__(self):
        """Initialize the mock auth service with in-memory storage."""
        self.users = {}  # {user_id: UserAuth}
        print("MockAuthService initialized with in-memory storage")
    
    def get_user_by_email(self, email: str) -> Optional[UserAuth]:
        """Get user by email address."""
        for user in self.users.values():
            if user.email == email:
                return user
        return None
    
    def get_user_by_google_id(self, google_id: str) -> Optional[UserAuth]:
        """Get user by Google ID."""
        for user in self.users.values():
            if user.google_id == google_id:
                return user
        return None
    
    def get_user_by_id(self, user_id: str) -> Optional[UserAuth]:
        """Get user by user ID."""
        return self.users.get(user_id)
    
    def create_user(self, user: UserAuth) -> UserAuth:
        """Create a new user."""
        self.users[user.user_id] = user
        return user
    
    def update_user(self, user: UserAuth) -> UserAuth:
        """Update an existing user."""
        self.users[user.user_id] = user
        return user
    
    def set_onboarded(self, user_id: str) -> None:
        """Mark a user as onboarded."""
        if user_id in self.users:
            self.users[user_id].is_onboarded = True
    
    def signup_with_email(self, email: str, password: str) -> Token:
        """Create a new user with email/password."""
        if self.get_user_by_email(email):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered"
            )
        
        import uuid
        user_id = f"user_{uuid.uuid4().hex[:12]}"
        
        user = UserAuth(
            user_id=user_id,
            email=email,
            password_hash=hash_password(password),
            is_onboarded=False,
        )
        
        self.create_user(user)
        access_token = create_access_token(user.user_id, user.email, user.is_onboarded)
        
        return Token(
            access_token=access_token,
            user_id=user.user_id,
            email=user.email,
            is_onboarded=user.is_onboarded,
        )
    
    def login_with_email(self, email: str, password: str) -> Token:
        """Authenticate with email/password."""
        user = self.get_user_by_email(email)
        
        if not user or not user.password_hash:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password"
            )
            
        if not verify_password(password, user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password"
            )
        
        access_token = create_access_token(user.user_id, user.email, user.is_onboarded)
        return Token(
            access_token=access_token,
            user_id=user.user_id,
            email=user.email,
            is_onboarded=user.is_onboarded,
        )
    
    def auth_with_google(self, credential: str) -> Token:
        """Mock Google Auth - accepts any non-empty credential as a user."""
        # Simple mock: treat credential as email if it looks like one, else mock it
        email = "mockuser@example.com"
        if "@" in credential:
            email = credential
            
        user = self.get_user_by_email(email)
        if not user:
            import uuid
            user_id = f"user_{uuid.uuid4().hex[:12]}"
            user = UserAuth(
                user_id=user_id,
                email=email,
                google_id="mock_google_id",
                is_onboarded=False
            )
            self.create_user(user)
            
        access_token = create_access_token(user.user_id, user.email, user.is_onboarded)
        return Token(
            access_token=access_token,
            user_id=user.user_id,
            email=user.email,
            is_onboarded=user.is_onboarded,
        )
