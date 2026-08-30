"""Pairing material, bearer verifier, and constant-time authentication."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from typing import Any

from .util import canonical_json

SCRYPT_N = 16_384
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SCRYPT_MAXMEM = 32 * 1024 * 1024

WORDS = (
    "acorn", "amber", "anchor", "apple", "april", "arch", "arrow", "atlas", "aurora", "bamboo", "beacon", "birch", "bloom", "blue", "breeze", "brook",
    "cabin", "cactus", "candle", "cedar", "charm", "cherry", "cloud", "clover", "comet", "coral", "cosmos", "crane", "creek", "dawn", "delta", "drift",
    "ember", "fern", "field", "finch", "fjord", "flame", "flora", "forest", "frost", "garden", "garnet", "glade", "glow", "grove", "harbor", "hazel",
    "heron", "hill", "honey", "iris", "island", "ivory", "jade", "jasmine", "juniper", "kite", "lagoon", "lake", "lantern", "lark", "laurel", "leaf",
    "lemon", "lilac", "lily", "lotus", "luna", "maple", "marble", "marigold", "meadow", "mint", "mist", "moon", "moss", "nectar", "night", "north",
    "nova", "oasis", "ocean", "olive", "opal", "orchid", "otter", "pearl", "pebble", "pine", "plum", "pond", "poppy", "quartz", "rain", "raven",
    "reed", "reef", "river", "robin", "rose", "ruby", "sage", "sand", "shell", "shore", "sky", "snow", "solar", "sparrow", "spring", "spruce",
    "star", "stone", "storm", "sun", "swift", "terra", "thistle", "tide", "timber", "trail", "tulip", "valley", "violet", "wave", "willow", "winter",
    "wren", "zephyr", "almond", "alpine", "bay", "berry", "bluff", "branch", "bronze", "buttercup", "canyon", "cascade", "chestnut", "citrine", "cliff", "coast",
    "copper", "cove", "crystal", "dahlia", "daisy", "dune", "elm", "falcon", "feather", "firefly", "fox", "ginger", "ginkgo", "glacier", "gold", "heather",
    "indigo", "kelp", "lavender", "light", "mango", "marsh", "mercury", "morning", "mountain", "myrtle", "oak", "orange", "orbit", "peach", "petal", "planet",
    "prairie", "rainbow", "ridge", "saffron", "sea", "silver", "slope", "solstice", "summit", "sunset", "teal", "thyme", "topaz", "vale", "vine", "wood",
    "ash", "aster", "azalea", "badger", "basil", "beech", "briar", "canary", "celery", "cobalt", "dove", "earth", "echo", "elmwood", "fig", "flint",
    "fog", "galaxy", "grass", "horizon", "ink", "jupiter", "koala", "lime", "lynx", "magnolia", "meteor", "nebula", "onyx", "peony", "perch", "quail",
    "ripple", "rosemary", "saturn", "seed", "shadow", "sierra", "sprout", "tangerine", "tiger", "umber", "velvet", "vista", "walnut", "water", "wild", "zinnia",
)


def random_token(byte_count: int = 32) -> str:
    return secrets.token_urlsafe(byte_count)


def random_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(16)}"


def client_instance_hash(instance_id: str) -> str:
    """Validate and irreversibly bind one browser installation identifier."""
    if not isinstance(instance_id, str) or not re.fullmatch(r"sci1_[A-Za-z0-9_-]{43}", instance_id):
        raise ValueError("client instance id is not valid")
    return hashlib.sha256(instance_id.encode("ascii")).hexdigest()


def new_credential() -> tuple[str, str]:
    # Hex deliberately excludes the underscore field delimiter. The secret
    # still carries a full 256 bits of entropy.
    lookup = secrets.token_hex(16)
    secret = secrets.token_hex(32)
    return lookup, f"sc1_{lookup}_{secret}"


def credential_lookup(credential: str) -> str | None:
    if not isinstance(credential, str) or len(credential) > 160:
        return None
    parts = credential.split("_")
    if len(parts) != 3 or parts[0] != "sc1" or not re.fullmatch(r"[0-9a-f]{32}", parts[1]) or not re.fullmatch(r"[0-9a-f]{64}", parts[2]):
        return None
    return parts[1]


def create_verifier(credential: str) -> dict[str, Any]:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        credential.encode("ascii"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
        dklen=SCRYPT_DKLEN, maxmem=SCRYPT_MAXMEM,
    )
    return {
        "algorithm": "scrypt",
        "salt": base64.b64encode(salt).decode("ascii"),
        "digest": base64.b64encode(digest).decode("ascii"),
        "n": SCRYPT_N,
        "r": SCRYPT_R,
        "p": SCRYPT_P,
        "dklen": SCRYPT_DKLEN,
    }


def verify_credential(credential: str, record: dict[str, Any]) -> bool:
    try:
        if record.get("algorithm") != "scrypt":
            return False
        salt = base64.b64decode(record["salt"], validate=True)
        expected = base64.b64decode(record["digest"], validate=True)
        digest = hashlib.scrypt(
            credential.encode("ascii"), salt=salt, n=int(record["n"]), r=int(record["r"]),
            p=int(record["p"]), dklen=int(record["dklen"]), maxmem=SCRYPT_MAXMEM,
        )
        return hmac.compare_digest(digest, expected)
    except (KeyError, TypeError, ValueError, UnicodeEncodeError):
        return False


def verification_phrase(secret: str, transcript: dict[str, Any]) -> list[str]:
    digest = hmac.new(secret.encode("ascii"), canonical_json(transcript), hashlib.sha256).digest()
    return [WORDS[int.from_bytes(digest[index * 2:index * 2 + 2], "big") % len(WORDS)] for index in range(3)]
