"""Audio call recording through LiveKit Egress, stored in S3-compatible storage.

Off unless RECORDING_ENABLED is set. Recording never affects a live call: any
failure here is logged and the call carries on unrecorded.
"""

import logging
from datetime import datetime
from typing import Optional

from livekit import api

from config.settings import get_settings

logger = logging.getLogger(__name__)


def recording_key(client_id: str, call_id: str) -> str:
    return f"recordings/{client_id}/{call_id}.ogg"


async def start_recording(lk: api.LiveKitAPI, room: str, client_id: str, call_id: str) -> Optional[str]:
    """Starts audio-only egress for the room. Returns the storage key, or None if not recording."""
    s = get_settings()
    if not (s.RECORDING_ENABLED and s.RECORDING_S3_BUCKET):
        return None
    key = recording_key(client_id, call_id)
    try:
        await lk.egress.start_room_composite_egress(api.RoomCompositeEgressRequest(
            room_name=room, audio_only=True,
            file_outputs=[api.EncodedFileOutput(
                file_type=api.EncodedFileType.OGG, filepath=key,
                s3=api.S3Upload(bucket=s.RECORDING_S3_BUCKET, region=s.RECORDING_S3_REGION,
                                endpoint=s.RECORDING_S3_ENDPOINT, access_key=s.RECORDING_S3_ACCESS_KEY,
                                secret=s.RECORDING_S3_SECRET_KEY))]))
        return key
    except Exception as e:
        logger.error(f"Recording not started for {call_id}: {e!r}")
        return None


def _s3():
    import boto3

    s = get_settings()
    return boto3.client("s3", region_name=s.RECORDING_S3_REGION, endpoint_url=s.RECORDING_S3_ENDPOINT or None,
                        aws_access_key_id=s.RECORDING_S3_ACCESS_KEY or None,
                        aws_secret_access_key=s.RECORDING_S3_SECRET_KEY or None)


def signed_recording_url(key: str, expires_seconds: int = 900) -> Optional[str]:
    """Short-lived link to one recording; the bucket itself stays private."""
    s = get_settings()
    if not (key and s.RECORDING_S3_BUCKET):
        return None
    try:
        return _s3().generate_presigned_url("get_object", Params={"Bucket": s.RECORDING_S3_BUCKET, "Key": key},
                                            ExpiresIn=expires_seconds)
    except Exception as e:
        logger.error(f"Recording link not created for {key}: {e!r}")
        return None


def delete_recording(key: str) -> bool:
    s = get_settings()
    try:
        _s3().delete_object(Bucket=s.RECORDING_S3_BUCKET, Key=key)
        return True
    except Exception as e:
        logger.error(f"Recording not deleted {key}: {e!r}")
        return False


def retention_cutoff(now: datetime) -> datetime:
    from datetime import timedelta

    return now - timedelta(days=get_settings().RECORDING_RETENTION_DAYS)
