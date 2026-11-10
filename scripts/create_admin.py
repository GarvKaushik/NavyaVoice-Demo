"""Creates (or resets the password of) an admin login for the portal.

    python -m scripts.create_admin you@example.com
"""

import argparse
import asyncio
import getpass
import sys
import uuid

from sqlalchemy import select

from campaign.auth import hash_password
from database.models import User
from database.session import AsyncSessionLocal


async def main(email: str, password: str) -> None:
    email = email.strip().lower()
    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar()
        if user is None:
            db.add(User(id=f"user_{uuid.uuid4().hex[:10]}", email=email, role="admin", password_hash=hash_password(password)))
        elif user.role != "admin":
            sys.exit(f"{email} is a client login; use a different email for the admin")
        else:
            user.password_hash = hash_password(password)
        await db.commit()
    print(f"Admin login ready: {email}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("email")
    args = ap.parse_args()
    secret = getpass.getpass("Password (at least 10 characters): ")
    if len(secret) < 10:
        sys.exit("Password must be at least 10 characters")
    asyncio.run(main(args.email, secret))
